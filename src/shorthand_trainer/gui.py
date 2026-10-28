from __future__ import annotations

import queue
import threading
import tkinter as tk
from dataclasses import replace
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .playback import AudioPlayer, output_devices
from .questions import QuestionOptions, generate_question
from .sessions import PracticeSession
from .settings import SettingsError, SettingsStore, default_settings_path
from .voicevox import AudioSettings, Cancelled, VoicevoxClient, render_audio


def _clock(seconds):
    minutes, seconds = divmod(int(seconds), 60)
    return f"{minutes:02d}:{seconds:02d}"


class Application:
    def __init__(self, root: tk.Tk, *, settings_path: Path | None = None):
        self.root = root
        root.title("速記トレーナー — 紙で学ぶ単語・文章練習")
        root.geometry(f"960x{min(860, max(600, root.winfo_screenheight() - 90))}")
        root.minsize(780, 600)
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.busy = False
        self.closed = False
        self.session: PracticeSession | None = None
        self.player: AudioPlayer | None = None
        self.voices = []
        self.connected_url = None
        self.devices = {"システム既定": None}
        self.settings_store = SettingsStore(
            settings_path if settings_path is not None else default_settings_path()
        )
        self.saved_settings = {}
        self.save_settings_enabled = True
        self._build()
        self._restore_settings()
        self._refresh_devices(quiet=True)
        self._refresh_controls()
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.timer_id = root.after(80, self._tick)

    def _build(self):
        outer = ttk.Frame(self.root)
        outer.pack(fill="both", expand=True)
        canvas = tk.Canvas(outer, highlightthickness=0)
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        canvas.configure(yscrollcommand=scrollbar.set)
        main = ttk.Frame(canvas, padding=12)
        body = canvas.create_window(0, 0, window=main, anchor="nw")

        def resize(event):
            canvas.itemconfigure(body, width=event.width)
            canvas.configure(scrollregion=canvas.bbox("all"))

        canvas.bind("<Configure>", resize)
        main.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))

        def scroll(event):
            if isinstance(event.widget, (tk.Text, ttk.Combobox, ttk.Spinbox)):
                return
            if main.winfo_height() > canvas.winfo_height():
                step = -1 if getattr(event, "num", None) == 4 else 1
                if getattr(event, "delta", 0):
                    step = -1 if event.delta > 0 else 1
                canvas.yview_scroll(step * 3, "units")

        self.root.bind("<MouseWheel>", scroll)
        self.root.bind("<Button-4>", scroll)
        self.root.bind("<Button-5>", scroll)
        ttk.Label(main, text="速記トレーナー", font=("", 18, "bold")).pack(anchor="w")
        ttk.Label(main, text="音声を聞いて紙に速記 → 紙で反訳 → 原文を開示して答え合わせ").pack(
            anchor="w", pady=(0, 8)
        )
        self.notebook = ttk.Notebook(main)
        self.notebook.pack(fill="x")
        self.notebook.bind("<<NotebookTabChanged>>", lambda _e: self._mode_changed())
        pattern = ttk.Frame(self.notebook, padding=8)
        words = ttk.Frame(self.notebook, padding=8)
        article = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(pattern, text="単語：パターン生成")
        self.notebook.add(words, text="単語：TXT 読込")
        self.notebook.add(article, text="文章：TXT 読込")
        self.pattern = tk.StringVar()
        ttk.Label(pattern, text="生成パターン").grid(row=0, column=0, sticky="w")
        ttk.Entry(pattern, textvariable=self.pattern).grid(row=0, column=1, sticky="ew", padx=8)
        pattern.columnconfigure(1, weight=1)
        ttk.Label(
            pattern,
            text="対応: ひらがな・ー・[あいう]・[あ-お]・?・{2,4}・(あ|い)。"
            "1文字もない結果は出題しません。",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=4)
        self.word_path, self.word_encoding = self._path_controls(words, directory=False)
        self.article_path, self.article_encoding = self._path_controls(article, directory=True)
        self.regex = tk.StringVar()
        ttk.Label(words, text="正規表現フィルタ").grid(row=2, column=0, sticky="w")
        ttk.Entry(words, textvariable=self.regex).grid(row=2, column=1, sticky="ew", padx=8)
        self.order = tk.StringVar(value="シャッフル")
        ttk.Combobox(
            words,
            textvariable=self.order,
            values=("シャッフル", "順出題"),
            state="readonly",
            width=14,
        ).grid(row=2, column=2)
        ttk.Label(words, text="通常の正規表現検索です。完全一致は ^…$。空行は除外します。").grid(
            row=3, column=0, columnspan=3, sticky="w", pady=4
        )
        ttk.Label(
            article, text="ディレクトリ直下の TXT から1本をランダムに選び、全文を読み上げます。"
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=4)

        options = ttk.LabelFrame(main, text="単語設定", padding=8)
        options.pack(fill="x", pady=8)
        self.count = tk.StringVar(value="20")
        self.duplicates = tk.BooleanVar(value=False)
        self.seed = tk.StringVar()
        ttk.Label(options, text="単語の出題数").pack(side="left")
        self.count_widget = ttk.Spinbox(
            options, from_=1, to=10000, width=7, textvariable=self.count
        )
        self.count_widget.pack(side="left", padx=6)
        self.duplicates_widget = ttk.Checkbutton(
            options, text="同じ単語の重複出題を許可する", variable=self.duplicates
        )
        self.duplicates_widget.pack(side="left", padx=8)
        ttk.Label(options, text="乱数シード（空欄は自動）").pack(side="left", padx=(12, 4))
        ttk.Entry(options, textvariable=self.seed, width=15).pack(side="left")

        sound = ttk.LabelFrame(main, text="音声・速度", padding=8)
        sound.pack(fill="x")
        sound.columnconfigure(1, weight=1)
        self.engine_url = tk.StringVar(value="http://127.0.0.1:50021")
        ttk.Label(sound, text="エンジンURL").grid(row=0, column=0, sticky="w")
        ttk.Entry(sound, textvariable=self.engine_url).grid(row=0, column=1, sticky="ew", padx=8)
        self.connect_button = ttk.Button(sound, text="接続して声を取得", command=self.connect)
        self.connect_button.grid(row=0, column=2)
        self.voice_name = tk.StringVar()
        ttk.Label(sound, text="声・スタイル").grid(row=1, column=0, sticky="w", pady=4)
        self.voice_box = ttk.Combobox(sound, textvariable=self.voice_name, state="readonly")
        self.voice_box.grid(row=1, column=1, columnspan=2, sticky="ew", padx=8)
        self.voice_box.bind("<<ComboboxSelected>>", lambda _e: self._voice_changed())
        self.credit = tk.StringVar()
        ttk.Label(sound, text="クレジット").grid(row=2, column=0, sticky="w")
        ttk.Entry(sound, textvariable=self.credit).grid(
            row=2, column=1, columnspan=2, sticky="ew", padx=8
        )
        speed = ttk.Frame(sound)
        speed.grid(row=3, column=0, columnspan=3, sticky="w", pady=(8, 0))
        self.speed_scale = tk.StringVar(value="1.0")
        ttk.Label(speed, text="VOICEVOX話速（倍率）").pack(side="left", padx=(0, 4))
        ttk.Spinbox(
            speed, from_=0.5, to=2.0, increment=0.05, textvariable=self.speed_scale, width=6
        ).pack(side="left", padx=(0, 12))
        ttk.Label(speed, text="0.5〜2.0倍・初期値1.0倍").pack(side="left")
        pauses = ttk.Frame(sound)
        pauses.grid(row=4, column=0, columnspan=3, sticky="w", pady=(8, 0))
        self.pause_cpm = tk.StringVar(value="60")
        self.comma_pause = tk.StringVar(value="0.5")
        self.sentence_pause = tk.StringVar(value="2.0")
        for label, variable, upper, increment in (
            ("休止の換算基準（字／分）", self.pause_cpm, 600, 1),
            ("読点の休止（字分）", self.comma_pause, 30, 0.1),
            ("句点・単語間（字分）", self.sentence_pause, 30, 0.1),
        ):
            ttk.Label(pauses, text=label).pack(side="left", padx=(0, 4))
            ttk.Spinbox(
                pauses,
                from_=1 if variable is self.pause_cpm else 0,
                to=upper,
                increment=increment,
                textvariable=variable,
                width=6,
            ).pack(side="left", padx=(0, 12))
        self.speed_hint = tk.StringVar()
        ttk.Label(sound, textvariable=self.speed_hint).grid(
            row=5, column=0, columnspan=3, sticky="w", pady=(4, 0)
        )
        for variable in (self.pause_cpm, self.comma_pause, self.sentence_pause):
            variable.trace_add("write", self._update_pause_hint)
        self._mode_changed()

        output = ttk.Frame(main)
        output.pack(fill="x", pady=8)
        self.log_root = tk.StringVar(value=str(Path.home() / "ShorthandTrainer" / "sessions"))
        ttk.Label(output, text="記録の保存先").pack(side="left")
        ttk.Entry(output, textvariable=self.log_root).pack(
            side="left", fill="x", expand=True, padx=8
        )
        ttk.Button(output, text="参照…", command=self._choose_logs).pack(side="left")

        actions = ttk.Frame(main)
        actions.pack(fill="x")
        self.generate_button = ttk.Button(actions, text="問題・音声を生成", command=self.generate)
        self.generate_button.pack(side="left")
        self.regenerate_button = ttk.Button(
            actions,
            text="同じ問題で音声を再生成",
            command=lambda: self.generate(reuse_question=True),
        )
        self.regenerate_button.pack(side="left", padx=8)
        self.cancel_button = ttk.Button(actions, text="生成をキャンセル", command=self.cancel.set)
        self.cancel_button.pack(side="left", padx=8)
        self.progress = ttk.Progressbar(actions, maximum=1)
        self.progress.pack(side="left", fill="x", expand=True)
        self.status = tk.StringVar(
            value="VOICEVOX ENGINE を起動し、「接続して声を取得」を押してください。"
        )
        ttk.Label(main, textvariable=self.status, wraplength=900).pack(anchor="w", pady=6)

        player_frame = ttk.LabelFrame(main, text="練習", padding=8)
        player_frame.pack(fill="x")
        device_row = ttk.Frame(player_frame)
        device_row.pack(fill="x")
        ttk.Label(device_row, text="音声出力").pack(side="left")
        self.device_name = tk.StringVar(value="システム既定")
        self.device_box = ttk.Combobox(device_row, textvariable=self.device_name, state="readonly")
        self.device_box.pack(side="left", fill="x", expand=True, padx=8)
        self.device_refresh = ttk.Button(device_row, text="更新", command=self._refresh_devices)
        self.device_refresh.pack(side="left")
        controls = ttk.Frame(player_frame)
        controls.pack(fill="x", pady=(8, 0))
        self.play_button = ttk.Button(controls, text="再生して練習開始", command=self.play)
        self.play_button.pack(side="left")
        self.pause_button = ttk.Button(controls, text="一時停止", command=self.pause)
        self.pause_button.pack(side="left", padx=6)
        self.abort_button = ttk.Button(controls, text="練習を中断", command=self.abort)
        self.abort_button.pack(side="left")
        self.reveal_button = ttk.Button(controls, text="反訳終了・原文を開示", command=self.reveal)
        self.reveal_button.pack(side="left", padx=6)
        self.elapsed = tk.StringVar(value="反訳 00:00")
        ttk.Label(controls, textvariable=self.elapsed).pack(side="right")
        self.play_progress = ttk.Progressbar(player_frame, maximum=1)
        self.play_progress.pack(fill="x", pady=(8, 0))
        self.summary = tk.StringVar(value="音声を生成すると、文字数・時間・実測分速を表示します。")
        ttk.Label(player_frame, textvariable=self.summary, wraplength=900).pack(
            anchor="w", pady=(4, 0)
        )

        answer_frame = ttk.LabelFrame(main, text="原文（反訳終了後に開示）", padding=4)
        answer_frame.pack(fill="both", expand=True, pady=(8, 0))
        scrollbar = ttk.Scrollbar(answer_frame)
        scrollbar.pack(side="right", fill="y")
        self.answer = tk.Text(
            answer_frame, height=6, wrap="word", font=("", 12), yscrollcommand=scrollbar.set
        )
        self.answer.pack(fill="both", expand=True)
        scrollbar.configure(command=self.answer.yview)
        self._set_answer("問題文は反訳終了後に表示されます。")

    def _settings_variables(self):
        names = (
            "pattern",
            "word_path",
            "word_encoding",
            "article_path",
            "article_encoding",
            "regex",
            "order",
            "count",
            "duplicates",
            "seed",
            "engine_url",
            "voice_name",
            "credit",
            "speed_scale",
            "pause_cpm",
            "comma_pause",
            "sentence_pause",
            "log_root",
            "device_name",
        )
        return {name: getattr(self, name) for name in names}

    def _restore_settings(self):
        try:
            self.saved_settings = self.settings_store.load()
        except (OSError, SettingsError) as exc:
            self.save_settings_enabled = False
            self.status.set(
                f"前回の設定を読み込めません: {exc} 初期設定で起動します。"
                f"元の設定は上書きしません: {self.settings_store.path}"
            )
            return
        choices = {
            "word_encoding": ("UTF-8（BOM可）", "CP932"),
            "article_encoding": ("UTF-8（BOM可）", "CP932"),
            "order": ("シャッフル", "順出題"),
        }
        for name, variable in self._settings_variables().items():
            value = self.saved_settings.get(name)
            if isinstance(variable, tk.BooleanVar):
                if isinstance(value, bool):
                    variable.set(value)
            elif isinstance(value, str) and (name not in choices or value in choices[name]):
                variable.set(value)
        mode = self.saved_settings.get("mode")
        if mode in ("pattern", "words", "article"):
            self.notebook.select(("pattern", "words", "article").index(mode))
        self._mode_changed()

    def _save_settings(self):
        if self.save_settings_enabled:
            values = self.saved_settings | {
                name: variable.get() for name, variable in self._settings_variables().items()
            }
            values["mode"] = ("pattern", "words", "article")[self.notebook.index("current")]
            self.settings_store.save(values)

    def _path_controls(self, parent, *, directory):
        path, encoding = tk.StringVar(), tk.StringVar(value="UTF-8（BOM可）")
        parent.columnconfigure(1, weight=1)
        ttk.Label(parent, text="ディレクトリ" if directory else "TXTファイル").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Entry(parent, textvariable=path).grid(row=0, column=1, sticky="ew", padx=8)

        def choose():
            selected = (
                filedialog.askdirectory(parent=self.root)
                if directory
                else filedialog.askopenfilename(
                    parent=self.root, filetypes=[("TXT", "*.txt"), ("すべて", "*")]
                )
            )
            if selected:
                path.set(selected)

        ttk.Button(parent, text="参照…", command=choose).grid(row=0, column=2)
        ttk.Label(parent, text="文字コード").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Combobox(
            parent,
            textvariable=encoding,
            values=("UTF-8（BOM可）", "CP932"),
            state="readonly",
            width=20,
        ).grid(row=1, column=1, sticky="w", padx=8)
        return path, encoding

    def _mode_changed(self):
        if not hasattr(self, "speed_hint"):
            return
        article = self.notebook.index("current") == 2
        self._update_pause_hint()
        self.count_widget.state(["disabled"] if article else ["!disabled"])
        self.duplicates_widget.state(["disabled"] if article else ["!disabled"])

    def _update_pause_hint(self, *_args):
        try:
            settings = AudioSettings(
                pause_cpm=float(self.pause_cpm.get()),
                comma_pause=float(self.comma_pause.get()),
                sentence_pause=float(self.sentence_pause.get()),
            )
            settings.validate()
        except ValueError:
            self.speed_hint.set("休止の秒数は、換算基準と字分を入力すると表示します。")
            return
        self.speed_hint.set(
            "休止（秒）= 字分 × 60 ÷ 換算基準　／　"
            f"読点 {settings.comma_pause * 60 / settings.pause_cpm:.2f}秒　／　"
            f"句点・単語間 {settings.sentence_pause * 60 / settings.pause_cpm:.2f}秒"
        )

    def _choose_logs(self):
        selected = filedialog.askdirectory(parent=self.root)
        if selected:
            self.log_root.set(selected)

    def _set_answer(self, text):
        self.answer.configure(state="normal")
        self.answer.delete("1.0", "end")
        self.answer.insert("1.0", text)
        self.answer.configure(state="disabled")

    def _refresh_devices(self, quiet=False):
        try:
            self.devices = {"システム既定": None} | {
                f"{index}: {name}": index for index, name in output_devices()
            }
            self.device_box.configure(values=list(self.devices))
            if self.device_name.get() not in self.devices:
                self.device_name.set("システム既定")
        except Exception as exc:
            if not quiet:
                self._error(f"音声デバイスを取得できません: {exc}")

    def _refresh_controls(self):
        state = self.session.state if self.session else None
        active = state in ("playing", "paused", "transcribing")
        enabled = {
            self.connect_button: not self.busy and not active,
            self.generate_button: not self.busy and not active,
            self.regenerate_button: not self.busy and not active and self.session is not None,
            self.cancel_button: self.busy,
            self.play_button: not self.busy and state == "prepared",
            self.pause_button: state in ("playing", "paused"),
            self.abort_button: not self.busy
            and state in ("prepared", "playing", "paused", "transcribing"),
            self.reveal_button: not self.busy and state in ("transcribing", "aborted"),
            self.device_refresh: not self.busy and not active,
        }
        for widget, enable in enabled.items():
            widget.state(["!disabled"] if enable else ["disabled"])
        self.pause_button.configure(text="再開" if state == "paused" else "一時停止")
        self.reveal_button.configure(
            text="原文を開示（中断）" if state == "aborted" else "反訳終了・原文を開示"
        )
        self.device_box.configure(state="disabled" if active else "readonly")

    def _error(self, message):
        self.status.set(str(message))
        messagebox.showerror("速記トレーナー", str(message), parent=self.root)

    def _background(self, work, done):
        if self.busy:
            return
        self.busy = True
        self.cancel.clear()
        self._refresh_controls()

        def worker():
            try:
                result = work()
                self.events.put(("done", done, result))
            except Exception as exc:
                self.events.put(("failure", exc))

        threading.Thread(target=worker, daemon=True).start()

    def connect(self):
        try:
            client = VoicevoxClient(self.engine_url.get())
        except ValueError as exc:
            self._error(exc)
            return
        self.status.set("VOICEVOX に接続しています…")

        def work():
            voices, version = client.voices(), client.version()
            if self.cancel.is_set():
                raise Cancelled("接続をキャンセルしました。")
            return voices, version

        def done(result):
            previous_voice = self.voice_name.get()
            previous_credit = self.credit.get()
            self.voices, version = result
            self.connected_url = client.url
            self.voice_box.configure(values=[v.label for v in self.voices])
            matching = next(
                (i for i, v in enumerate(self.voices) if v.label == previous_voice), None
            )
            index = (
                matching
                if matching is not None
                else next((i for i, v in enumerate(self.voices) if v.style == "ノーマル"), 0)
            )
            self.voice_box.current(index)
            if matching is not None and previous_credit:
                self.credit.set(previous_credit)
            else:
                self._voice_changed()
            self.status.set(f"接続しました: VOICEVOX ENGINE {version} / {len(self.voices)}スタイル")

        self._background(work, done)

    def _voice_changed(self):
        voice = next((v for v in self.voices if v.label == self.voice_name.get()), None)
        if voice:
            self.credit.set(voice.credit)

    def generate(self, *, reuse_question=False):
        if self.busy or (
            self.session and self.session.state in ("playing", "paused", "transcribing")
        ):
            return
        try:
            reused = self.session.question if reuse_question and self.session else None
            if reuse_question and reused is None:
                raise ValueError("先に問題を生成してください。")
            client = VoicevoxClient(self.engine_url.get())
            voice = next((v for v in self.voices if v.label == self.voice_name.get()), None)
            if voice is None or client.url != self.connected_url:
                raise ValueError("先に「接続して声を取得」を押し、声を選択してください。")
            if not self.credit.get().strip():
                raise ValueError("選択した声のクレジットを入力してください。")
            voice = replace(voice, credit_text=self.credit.get().strip())
            options = None
            if reused is None:
                mode = ("pattern", "words", "article")[self.notebook.index("current")]
                path = self.article_path.get() if mode == "article" else self.word_path.get()
                if mode != "pattern" and not path.strip():
                    raise ValueError("読み込む TXT またはディレクトリを指定してください。")
                encoding = (
                    self.article_encoding.get() if mode == "article" else self.word_encoding.get()
                )
                options = QuestionOptions(
                    mode=mode,
                    pattern=self.pattern.get(),
                    path=path,
                    encoding="cp932" if encoding == "CP932" else "utf-8-sig",
                    regex=self.regex.get(),
                    count=int(self.count.get()) if mode != "article" else 1,
                    duplicates=self.duplicates.get(),
                    order="sequential" if self.order.get() == "順出題" else "shuffle",
                    seed=int(self.seed.get()) if self.seed.get().strip() else None,
                )
            settings = AudioSettings(
                speed_scale=float(self.speed_scale.get()),
                comma_pause=float(self.comma_pause.get()),
                sentence_pause=float(self.sentence_pause.get()),
                pause_cpm=float(self.pause_cpm.get()),
            )
            settings.validate()
            if not self.log_root.get().strip():
                raise ValueError("記録の保存先を指定してください。")
            root = Path(self.log_root.get()).expanduser()
        except ValueError as exc:
            self._error(f"設定を確認してください: {exc}")
            return
        self.progress["value"] = 0
        self.status.set("問題を生成しています…")

        def work():
            question = reused if reused is not None else generate_question(options)
            audio = render_audio(
                question,
                settings,
                client,
                voice,
                cancel=self.cancel,
                progress=lambda f, m: self.events.put(("progress", f, m)),
            )
            if self.cancel.is_set():
                raise Cancelled("音声生成をキャンセルしました。")
            session = PracticeSession.create(root, question, audio)
            if self.cancel.is_set():
                session.abort()
                raise Cancelled("音声生成をキャンセルしました。")
            return session

        def done(session):
            previous = self.session
            self.session = session
            if previous:
                try:
                    previous.abort()
                except OSError as exc:
                    self._error(f"前回の中断状態を保存できませんでした: {exc}")
            self._set_answer("問題文は反訳終了後に表示されます。")
            self.play_progress["value"] = 0
            self.elapsed.set("反訳 00:00")
            audio = session.audio
            summary = (
                f"{session.question.characters}字 / 音声 {audio.duration:.2f}秒 "
                f"({_clock(audio.duration)}) / VOICEVOX話速 {audio.speed_scale:g}倍\n"
                f"実測分速: 休止除く {audio.spoken_cpm:.1f}、休止込み {audio.overall_cpm:.1f}字/分 "
                f"/ 休止合計 {audio.pause_seconds:.2f}秒"
            )
            self.summary.set(summary)
            self.status.set(f"準備完了。再生して練習を開始できます。保存先: {session.directory}")

        self._background(work, done)

    def play(self):
        if not self.session or self.session.state != "prepared" or self.busy:
            return
        try:
            player = AudioPlayer(
                self.session.audio.samples,
                self.session.audio.sample_rate,
                self.devices.get(self.device_name.get()),
                lambda: self.events.put(("finished", player)),
                lambda error: self.events.put(("play_error", player, error)),
            )
            self.player = player
            player.start()
            self.session.start_playback()
            self.status.set("再生中です。音声を聞いて紙に速記してください。")
        except Exception as exc:
            if self.player:
                self.player.stop()
                self.player = None
            self._error(f"再生を開始できません。音声出力と保存先を確認してください: {exc}")
        finally:
            self._refresh_controls()

    def pause(self):
        if not self.player or not self.session:
            return
        try:
            paused = self.session.state == "playing"
            if paused:
                self.session.pause()
                self.status.set("一時停止中です。")
            else:
                self.session.resume()
                self.status.set("再生を再開しました。")
            self.player.paused = paused
        except Exception as exc:
            self._error(exc)
        self._refresh_controls()

    def _finished(self, player):
        if player is not self.player or not self.session:
            return
        self.player.stop()
        self.player = None
        # The last buffer can finish just as the user presses pause.
        if self.session.state == "paused":
            self.session.resume()
        self.session.finish_playback()
        self.play_progress["value"] = 1
        self.status.set("再生が終了しました。紙で反訳してください。終了したら原文を開示します。")
        self._refresh_controls()

    def abort(self):
        try:
            if self.player:
                self.player.stop()
                self.player = None
            if self.session:
                self.session.abort()
            self.status.set("練習を中断しました。原文を開示するか、次の問題を生成できます。")
        except Exception as exc:
            self._error(exc)
        self._refresh_controls()

    def reveal(self):
        if not self.session:
            return
        try:
            self._set_answer(self.session.reveal())
            self.elapsed.set(f"反訳 {_clock(self.session.transcription_seconds)}")
            self.status.set(f"原文を開示しました。記録: {self.session.directory}")
        except Exception as exc:
            self._error(exc)
        self._refresh_controls()

    def _tick(self):
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind in ("done", "failure"):
                    self.busy = False
                    if kind == "done":
                        event[1](event[2])
                    elif isinstance(event[1], Cancelled):
                        self.status.set(str(event[1]))
                    else:
                        self._error(event[1])
                    self._refresh_controls()
                elif kind == "progress":
                    self.progress["value"] = event[1]
                    self.status.set(event[2])
                elif kind == "finished":
                    self._finished(event[1])
                elif kind == "play_error" and event[1] is self.player:
                    self.abort()
                    self._error(f"再生中にエラーが発生しました: {event[2]}")
        except queue.Empty:
            pass
        except Exception as exc:
            self._error(exc)
            self._refresh_controls()
        if self.player and self.session:
            self.play_progress["value"] = self.player.position / len(self.session.audio.samples)
        if self.session and self.session.state == "transcribing":
            self.elapsed.set(f"反訳 {_clock(self.session.transcription_seconds)}")
        if not self.closed:
            self.timer_id = self.root.after(80, self._tick)

    def close(self):
        self.closed = True
        self.cancel.set()
        if self.player:
            self.player.stop()
        if self.session:
            try:
                self.session.abort()
            except OSError as exc:
                self._error(f"終了時の記録を保存できませんでした: {exc}")
        try:
            self._save_settings()
        except (OSError, SettingsError) as exc:
            self._error(f"入力設定を保存できませんでした: {exc}")
        self.root.after_cancel(self.timer_id)
        self.root.destroy()


def run():
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        raise RuntimeError(
            "GUIを開始できません。デスクトップ環境とTkのインストールを確認してください。"
        ) from exc
    Application(root)
    root.mainloop()
