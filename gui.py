"""
Desktop GUI for the PDF line extractor. All the actual PDF/CSV work lives
in core.py - this file is just the Tkinter window around it.

Run it with: python gui.py
"""

import os
import queue
import threading

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

from core import run_extraction

DEFAULT_URL_COLUMN = "PDF_URL"
DEFAULT_TIMEOUT_SECONDS = 5
DEFAULT_BATCH_SIZE = 100


class ExtractorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("PDF Lines Extractor")
        self.root.geometry("900x750")
        self.root.minsize(650, 520)

        self.stop_event = threading.Event()
        self.worker_thread = None
        self.log_queue = queue.Queue()

        self._build_ui()
        self.root.after(100, self._poll_log_queue)

    # UI layout
    def _build_ui(self):
        pad = {"padx": 10, "pady": 6}

        main = ttk.Frame(self.root)
        main.pack(fill="both", expand=True)

        settings = ttk.LabelFrame(main, text="Settings")
        settings.pack(fill="x", **pad)

        # input file
        ttk.Label(settings, text="Input file (Excel):").grid(row=0, column=0, sticky="w", padx=(8, 2), pady=6)
        self.input_var = tk.StringVar()
        ttk.Entry(settings, textvariable=self.input_var, width=55).grid(row=0, column=1, padx=2, pady=6)
        ttk.Button(settings, text="Browse...", command=self._browse_input).grid(row=0, column=2, padx=(2, 8))

        # output file
        ttk.Label(settings, text="Output file (CSV):").grid(row=1, column=0, sticky="w", padx=(8, 2), pady=6)
        self.output_var = tk.StringVar()
        ttk.Entry(settings, textvariable=self.output_var, width=55).grid(row=1, column=1, padx=2, pady=6)
        ttk.Button(settings, text="Browse...", command=self._browse_output).grid(row=1, column=2, padx=(2, 8))

        # url column name
        ttk.Label(settings, text="URL column name:").grid(row=2, column=0, sticky="w", padx=(8, 2), pady=6)
        self.column_var = tk.StringVar(value=DEFAULT_URL_COLUMN)
        ttk.Entry(settings, textvariable=self.column_var, width=20).grid(row=2, column=1, sticky="w", padx=2, pady=6)

        # timeout - see README for why 5s is the default
        ttk.Label(settings, text="Timeout per file (seconds):").grid(row=3, column=0, sticky="w", padx=(8, 2), pady=6)
        timeout_frame = ttk.Frame(settings)
        timeout_frame.grid(row=3, column=1, columnspan=2, sticky="w", padx=2, pady=6)
        self.timeout_var = tk.IntVar(value=DEFAULT_TIMEOUT_SECONDS)
        ttk.Spinbox(timeout_frame, from_=1, to=600, textvariable=self.timeout_var, width=10).pack(side="left")
        ttk.Label(
            timeout_frame, text="(external / internet PDFs usually need more than this - see README)",
            foreground="#888888",
        ).pack(side="left", padx=(6, 0))

        # batch size
        ttk.Label(settings, text="Batch size (save every N files):").grid(row=4, column=0, sticky="w", padx=(8, 2), pady=6)
        self.batch_var = tk.IntVar(value=DEFAULT_BATCH_SIZE)
        ttk.Spinbox(settings, from_=1, to=5000, textvariable=self.batch_var, width=10).grid(
            row=4, column=1, sticky="w", padx=4, pady=6
        )

        # start / stop
        btns = ttk.Frame(main)
        btns.pack(fill="x", **pad)
        self.start_btn = ttk.Button(btns, text="Start", command=self._on_start)
        self.start_btn.pack(side="left", padx=4)
        self.stop_btn = ttk.Button(btns, text="Stop", command=self._on_stop, state="disabled")
        self.stop_btn.pack(side="left", padx=4)

        # progress bar
        prog_frame = ttk.Frame(main)
        prog_frame.pack(fill="x", **pad)
        self.progress = ttk.Progressbar(prog_frame, orient="horizontal", mode="determinate")
        self.progress.pack(fill="x", side="left", expand=True)
        self.progress_label = ttk.Label(prog_frame, text="0 / 0")
        self.progress_label.pack(side="left", padx=8)

        # log
        log_frame = ttk.LabelFrame(main, text="Log")
        log_frame.pack(fill="both", expand=True, **pad)
        self.log_box = scrolledtext.ScrolledText(log_frame, wrap="word", height=15, state="disabled")
        self.log_box.pack(fill="both", expand=True, padx=6, pady=6)

        # status + credit
        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(main, textvariable=self.status_var, anchor="w").pack(fill="x", padx=10, pady=(0, 2))
        ttk.Label(main, text="Developed by BaHaa", anchor="center", foreground="#888888").pack(
            fill="x", padx=10, pady=(0, 8)
        )

    # file pickers
    def _browse_input(self):
        path = filedialog.askopenfilename(
            title="Select the input Excel file",
            filetypes=[("Excel files", "*.xlsx *.xls"), ("All files", "*.*")],
        )
        if path:
            self.input_var.set(path)
            if not self.output_var.get():
                self.output_var.set(os.path.join(os.path.dirname(path), "output.csv"))

    def _browse_output(self):
        path = filedialog.asksaveasfilename(
            title="Choose where to save the output CSV",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if path:
            self.output_var.set(path)

    # start / stop

    def _on_start(self):
        input_path = self.input_var.get().strip()
        output_path = self.output_var.get().strip()
        url_column = self.column_var.get().strip() or DEFAULT_URL_COLUMN

        if not input_path:
            messagebox.showwarning("Missing input", "Please select an input file first.")
            return
        if not output_path:
            messagebox.showwarning("Missing output", "Please choose where to save the output.")
            return

        params = {
            "input_path": input_path,
            "output_path": output_path,
            "url_column": url_column,
            "timeout": int(self.timeout_var.get()),
            "batch_size": int(self.batch_var.get()),
        }

        self._clear_log()
        self.stop_event.clear()
        self.progress["value"] = 0
        self.progress_label.config(text="0 / 0")
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.status_var.set("Running...")

        self.worker_thread = threading.Thread(
            target=run_extraction,
            args=(params, self.stop_event, self._log, self._update_progress, self._on_done),
            daemon=True,
        )
        self.worker_thread.start()

    def _on_stop(self):
        self.stop_event.set()
        self.status_var.set("Stopping after the current batch finishes...")
        self.stop_btn.config(state="disabled")

    def _on_done(self, success):
        def update():
            self.start_btn.config(state="normal")
            self.stop_btn.config(state="disabled")
            self.status_var.set("Done." if success else "Stopped, or an error occurred - check the log.")
        self.root.after(0, update)

    # thread-safe logging / progress (worker thread -> queue -> UI thread)
    def _log(self, message):
        self.log_queue.put(("log", message))

    def _update_progress(self, done, total):
        self.log_queue.put(("progress", (done, total)))

    def _poll_log_queue(self):
        try:
            while True:
                kind, payload = self.log_queue.get_nowait()
                if kind == "log":
                    self.log_box.config(state="normal")
                    self.log_box.insert("end", payload + "\n")
                    self.log_box.see("end")
                    self.log_box.config(state="disabled")
                elif kind == "progress":
                    done, total = payload
                    self.progress["maximum"] = max(total, 1)
                    self.progress["value"] = done
                    self.progress_label.config(text=f"{done} / {total}")
        except queue.Empty:
            pass
        self.root.after(100, self._poll_log_queue)

    def _clear_log(self):
        self.log_box.config(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.config(state="disabled")


def main():
    root = tk.Tk()
    try:
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
        elif "clam" in style.theme_names():
            style.theme_use("clam")
    except Exception:
        pass
    ExtractorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
