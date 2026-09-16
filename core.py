# reads the excel file, pulls text from each PDF, writes to CSV in batches
# kept separate from gui.py so it can be reused/tested without tkinter

# needs a Customs module (not included here) with a Fitz_Parse_Safe(urls, how="line")
# function - see README for the expected interface

import os
import gc
import warnings
from concurrent.futures import ProcessPoolExecutor, TimeoutError

warnings.filterwarnings("ignore")

try:
    import Customs
    CUSTOMS_IMPORT_ERROR = None
except Exception as e:
    # don't crash here - show the error in the log once the user hits Start
    Customs = None
    CUSTOMS_IMPORT_ERROR = str(e)


def process_batch(pdf_urls, seconds_to_wait, stop_event, log_fn):
    # each url runs in its own subprocess with a timeout, so one slow/dead
    # PDF doesn't hang the whole batch - failed ones just get None
    results = {}
    with ProcessPoolExecutor(max_workers=1) as executor:
        for url in pdf_urls:
            if stop_event.is_set():
                break
            try:
                future = executor.submit(Customs.Fitz_Parse_Safe, [url], how="line")
                res = future.result(timeout=seconds_to_wait)
                if res and url in res:
                    results[url] = res[url]
            except TimeoutError:
                results[url] = None
                log_fn(f"  Timeout: {url}")
            except Exception as e:
                results[url] = None
                log_fn(f"  Error on {url}: {e}")
            gc.collect()
    return results


def run_extraction(params, stop_event, log_fn, progress_fn, done_fn):
    # runs on a background thread, called from gui.py
    # params: input_path, output_path, url_column, timeout, batch_size
    import pandas as pd  # imported here so a missing dependency shows in the log, not a crash on startup

    try:
        input_path = params["input_path"]
        output_path = params["output_path"]
        url_column = params["url_column"]
        seconds_to_wait = params["timeout"]
        batch_size = params["batch_size"]

        if Customs is None:
            log_fn(f"Could not import the Customs module: {CUSTOMS_IMPORT_ERROR}")
            log_fn("Make sure Customs.py is next to this script (or on your PYTHONPATH).")
            done_fn(success=False)
            return

        if not os.path.exists(input_path):
            log_fn("Input file not found.")
            done_fn(success=False)
            return

        log_fn(f"Reading input file: {input_path}")
        df_input = pd.read_excel(input_path, dtype=str)

        if url_column not in df_input.columns:
            log_fn(f"Column '{url_column}' not found. Available columns: {list(df_input.columns)}")
            done_fn(success=False)
            return

        unique_pdfs = df_input[url_column].dropna().unique()
        total_unique = len(unique_pdfs)
        log_fn(f"Found {total_unique} unique PDF URLs to process")

        if os.path.exists(output_path):
            os.remove(output_path)
            log_fn("Removed the existing output file - starting a clean run")

        for i in range(0, total_unique, batch_size):
            if stop_event.is_set():
                log_fn("Stopped by user.")
                break

            batch_urls = unique_pdfs[i:i + batch_size]
            batch_results = process_batch(batch_urls, seconds_to_wait, stop_event, log_fn)

            # one row per line, original excel columns stay attached
            batch_rows = []
            for url in batch_urls:
                content = batch_results.get(url)
                original_data = df_input[df_input[url_column] == url]

                if content:
                    for p_idx, page in enumerate(content):
                        for l_idx, line in enumerate(page):
                            for _, row in original_data.iterrows():
                                new_row = row.to_dict()
                                new_row.update({"page": p_idx, "line": l_idx, "line_data": line})
                                batch_rows.append(new_row)
                else:
                    # failed/timed out - keep a row anyway so nothing just disappears
                    for _, row in original_data.iterrows():
                        new_row = row.to_dict()
                        new_row.update({"page": None, "line": None, "line_data": None})
                        batch_rows.append(new_row)

            # write as we go, so a crash mid-run doesn't lose everything
            if batch_rows:
                pd.DataFrame(batch_rows).to_csv(
                    output_path,
                    mode="a",
                    index=False,
                    header=not os.path.exists(output_path),
                    encoding="utf-8-sig",
                )

            del batch_results, batch_rows
            gc.collect()

            done_count = min(i + batch_size, total_unique)
            progress_fn(done_count, total_unique)
            log_fn(f"Progress: {done_count}/{total_unique} saved to {os.path.basename(output_path)}")

        if not stop_event.is_set():
            log_fn("-" * 30)
            log_fn(f"Done. Output saved to: {output_path}")
        done_fn(success=not stop_event.is_set())

    except Exception as e:
        log_fn(f"Unexpected error: {e}")
        done_fn(success=False)
