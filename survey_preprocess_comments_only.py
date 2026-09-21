import argparse
import datetime
import os
import re
import sys

import openpyxl
import pandas as pd
from google.cloud import storage


def main(args):
    lan_proj_id = args.landing_project
    arc_proj_id = args.staging_project
    landing_bucket_name = args.landing_bucket
    archive_bucket_name = args.archive_bucket
    file_pattern = args.file_name
    date_columns = [c.strip() for c in args.date_columns.split(';') if c.strip()] if args.date_columns else []
    datetime_columns = [c.strip() for c in args.datetime_columns.split(';') if c.strip()] if args.datetime_columns else []

    # Initialize GCS clients for both the landing and archive projects
    landing_client = storage.Client(project=lan_proj_id)
    archive_client = storage.Client(project=arc_proj_id)

    # Define GCS folder paths for input and output
    folder_path = 'SFG_Generic'
    archive_folder_path = 'GLINT/ORGINAL'
    output_file_path = 'SFG_Generic'

    # Define local temporary paths for file processing
    local_temp_input = "/tmp/" + file_pattern + ".xlsx"
    print(
        f"Configuration: Landing Project='{lan_proj_id}', Archive Project='{arc_proj_id}', "
        f"Landing Bucket='{landing_bucket_name}', Archive Bucket='{archive_bucket_name}', "
        f"File Pattern='{file_pattern}'"
    )

    # Get the list of all files in the specified landing bucket folder
    landing_bucket = landing_client.bucket(landing_bucket_name)
    archive_bucket = archive_client.bucket(archive_bucket_name)
    blobs = list(landing_bucket.list_blobs(prefix=folder_path))

    # Find all files that match the pattern
    def download_files(blobs, file_pattern):
        files_to_process = []
        for blob in blobs:
            input_file_name = blob.name
            print(f"Checking file: {input_file_name}")

            if re.search(file_pattern, input_file_name) and not input_file_name.endswith('/'):
                # Generate a unique temporary input path for each file
                base_name = os.path.basename(input_file_name)
                local_temp_input = os.path.join("/tmp/", base_name)

                print(f"Found matching file: {input_file_name}. Downloading to {local_temp_input}")
                blob.download_to_filename(local_temp_input)

                # Generate the output file name
                output_file_name = os.path.splitext(base_name)[0] + '.csv'
                local_output_path = os.path.join('/tmp', output_file_name)

                files_to_process.append({
                    "input_file": os.path.abspath(local_temp_input),
                    "output_name": output_file_name,
                    "output_file": local_output_path,
                    "original_blob": blob
                })

        return files_to_process

    # Download all target files and get their local paths
    files_to_process = download_files(blobs, file_pattern)
    print(f"Found {len(files_to_process)} files to process.")

    # Exit if no files were found
    if not files_to_process:
        print(
            f"Error: No file matching pattern '{file_pattern}' found in "
            f"'gs://{landing_bucket_name}/{folder_path}'."
        )
        sys.exit(1)

    def sanitize_comment_value(value):
        """
        Sanitize free-text comment values ONLY.

        Applied exclusively to columns whose Excel header ends with _COMMENTS.

        Rules:
        - Preserve nulls as empty output values.
        - Replace CR/LF line breaks with a single space so one comment cannot
          split a physical output record.
        - Replace tabs and unsafe control characters with spaces.
        - Protect the multi-character output delimiter || when it occurs
          naturally inside free comments.
        - Replace ASCII double quote (") with Unicode right double quote (”)
          so the fixed Spark CSV reader does not interpret it as CSV quoting.
        - Trim leading/trailing whitespace.

        Unicode letters, punctuation, emojis, apostrophes, single pipes, etc.
        are otherwise preserved.
        """
        if value is None or pd.isna(value):
            return ""

        text_value = str(value)

        # Free comments can contain copied/pasted multi-line text.
        text_value = re.sub(r'[\r\n]+', ' ', text_value)

        # Tabs can create inconsistent raw text records.
        text_value = re.sub(r'[\t]+', ' ', text_value)

        # Remove remaining unsafe ASCII control characters while preserving
        # normal Unicode text and punctuation.
        text_value = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', ' ', text_value)

        # Protect the actual file delimiter if a user typed it in a comment.
        # A single "|" remains untouched.
        text_value = text_value.replace('||', '| |')

        # Spark is configured to interpret ASCII " as its quote character.
        # Unicode ” remains readable for sentiment analysis but is not the
        # same character as ASCII ".
        text_value = text_value.replace('"', '\u201D')

        return text_value.strip()

    def resolve_split_record_issue(df, comment_indices):
        """
        Apply split-record/free-text cleanup ONLY to _COMMENTS columns.

        Every non-comment column is deliberately skipped.
        """
        print("Resolving free-comment record issues in _COMMENTS columns only...")

        if df.empty:
            print("Comment cleanup skipped because DataFrame is empty.")
            return df

        if not comment_indices:
            print("No _COMMENTS columns found. No free-text cleanup applied.")
            return df

        cleaned_cells = 0

        for col_idx in sorted(comment_indices):
            if col_idx not in df.columns:
                print(
                    f"Warning: comment column index {col_idx} does not exist "
                    f"in DataFrame and will be skipped."
                )
                continue

            def needs_cleanup(value):
                if value is None or pd.isna(value):
                    return False
                original = str(value)
                return sanitize_comment_value(value) != original

            mask = df[col_idx].apply(needs_cleanup)
            affected_count = int(mask.sum())
            cleaned_cells += affected_count

            if affected_count > 0:
                df.loc[mask, col_idx] = df.loc[mask, col_idx].apply(
                    sanitize_comment_value
                )

        print(
            f"Comment cleanup completed. "
            f"_COMMENTS cells cleaned: {cleaned_cells}"
        )

        return df

    def csv_from_excel(file_info, specific_text, date_columns, datetime_columns):
        wb = openpyxl.load_workbook(file_info["input_file"], data_only=True)
        sheet = wb.active

        def normalize_date_only(value):
            """Convert supported date-only strings (day-first or month-first) to YYYY-MM-DD."""
            candidate_formats = ["%d/%m/%Y", "%m/%d/%Y"]
            for date_format in candidate_formats:
                try:
                    return datetime.datetime.strptime(value, date_format).strftime('%Y-%m-%d')
                except ValueError:
                    continue
            return value

        def normalize_datetime_value(value):
            """Convert supported date/time strings (day-first or month-first) to YYYY-MM-DD HH:MM:SS."""
            candidate_formats = [
                "%d/%m/%Y %H:%M:%S",
                "%d/%m/%Y %H:%M",
                "%m/%d/%Y %H:%M:%S",
                "%m/%d/%Y %H:%M",
            ]
            for date_format in candidate_formats:
                try:
                    return datetime.datetime.strptime(value, date_format).strftime('%Y-%m-%d %H:%M:%S')
                except ValueError:
                    continue
            return value

        def resolve_target_column_indices(sheet, column_names):
            """Map requested Excel column header names to their 0-based indices."""
            if not column_names:
                return set()

            header_row = next(sheet.iter_rows(min_row=1, max_row=1))
            header_map = {
                str(cell.value).strip().lower(): idx
                for idx, cell in enumerate(header_row)
                if cell.value is not None
            }

            target_indices = set()
            missing = []
            for name in column_names:
                key = name.strip().lower()
                if key in header_map:
                    target_indices.add(header_map[key])
                else:
                    missing.append(name)

            if missing:
                print(f"Warning: column(s) not found in header and will be ignored: {missing}")

            return target_indices

        date_indices = resolve_target_column_indices(sheet, date_columns)
        datetime_indices = resolve_target_column_indices(sheet, datetime_columns)

        # ------------------------------------------------------------------
        # ADDED: Identify ONLY columns whose Excel header ends with _COMMENTS.
        # These column indexes are used later to replace ASCII double quotes
        # with Unicode quotes before the ||-delimited file is written.
        # ------------------------------------------------------------------
        header_row = next(sheet.iter_rows(min_row=1, max_row=1))
        comment_indices = {
            idx
            for idx, cell in enumerate(header_row)
            if cell.value is not None
            and str(cell.value).strip().upper().endswith("_COMMENTS")
        }

        print(
            f"Comment column index(es) requiring double-quote conversion: "
            f"{sorted(comment_indices)}"
        )

        overlap = date_indices & datetime_indices
        if overlap:
            print(
                f"Warning: column index(es) {sorted(overlap)} were requested as both date and datetime; "
                f"datetime formatting will take precedence."
            )
            date_indices -= overlap

        print(f"Date-only conversion will be applied to column index(es): {sorted(date_indices)}")
        print(f"Datetime conversion will be applied to column index(es): {sorted(datetime_indices)}")

        data = []
        for row in sheet.iter_rows():
            first_cell_value = str(row[0].value).strip() if row[0].value is not None else ""
            if first_cell_value.lower() != specific_text.lower():
                formatted_row = []
                for cell_idx, cell in enumerate(row):
                    if cell.value is None:
                        formatted_row.append("")
                    elif cell_idx in datetime_indices:
                        if isinstance(cell.value, datetime.datetime):
                            formatted_row.append(cell.value.strftime('%Y-%m-%d %H:%M:%S'))
                        elif isinstance(cell.value, datetime.date):
                            formatted_row.append(
                                datetime.datetime.combine(
                                    cell.value, datetime.time.min
                                ).strftime('%Y-%m-%d %H:%M:%S')
                            )
                        else:
                            formatted_row.append(normalize_datetime_value(str(cell.value)))
                    elif cell_idx in date_indices:
                        if isinstance(cell.value, (datetime.datetime, datetime.date)):
                            formatted_row.append(cell.value.strftime('%Y-%m-%d'))
                        else:
                            formatted_row.append(normalize_date_only(str(cell.value)))
                    else:
                        # Column not requested for date/datetime conversion: leave untouched.
                        formatted_row.append(str(cell.value))
                data.append(formatted_row)

        if data and all(cell == "" for cell in data[-1]):
            data.pop()

        df = pd.DataFrame(data)

        print(f"Input file row count before split record cleanup: {len(df)}")

        # Resolve records appearing across multiple physical lines because
        # an Excel cell contains carriage-return or line-feed characters.
        df = resolve_split_record_issue(df, comment_indices)

        print(f"Output file row count after split record cleanup: {len(df)}")

        required_delimiter = '||'

        # ------------------------------------------------------------------
        # Write the ||-delimited file.
        #
        # IMPORTANT:
        #   _COMMENTS columns -> free-text sanitizer is applied.
        #   Every other column -> written without line-break/tab/quote/space/
        #                         delimiter replacement.
        #
        # Existing requested date/datetime conversions above still apply.
        # ------------------------------------------------------------------
        quote_replacement_count = 0

        # Count raw ASCII quotes remaining in comment fields before the final
        # write. Normally these may already have been converted by the cleanup
        # step, but this also acts as a final safety check.
        for col_idx in sorted(comment_indices):
            if col_idx in df.columns:
                quote_replacement_count += int(
                    df[col_idx]
                    .dropna()
                    .astype(str)
                    .str.count('"')
                    .sum()
                )

        with open(
            file_info["output_file"],
            'w',
            encoding='utf-8',
            newline=''
        ) as file:
            for _, row in df.iterrows():
                output_values = []

                for col_idx, value in enumerate(row.values):
                    if value is None or pd.isna(value):
                        output_values.append("")
                    elif col_idx in comment_indices:
                        # Robust free-comment cleanup happens ONLY here.
                        output_values.append(
                            sanitize_comment_value(value)
                        )
                    else:
                        # Do not strip, replace line breaks/tabs, modify quotes,
                        # alter ||, or otherwise sanitize non-comment columns.
                        output_values.append(str(value))

                file.write(
                    required_delimiter.join(output_values) + '\n'
                )

        print(
            "Final ASCII double quotes converted in _COMMENTS columns "
            f"during write: {quote_replacement_count}"
        )
        print(
            "Non-comment columns were excluded from free-text cleanup."
        )
        print(f"Processed file saved as: {file_info['output_file']}")

    def move_to_archive(file_info, archive_bucket):
        original_blob = file_info["original_blob"]
        archive_file_path = os.path.join(archive_folder_path, os.path.basename(original_blob.name))

        print(f"Archiving {original_blob.name} to {archive_file_path}")

        # Correctly copy blob to new location
        destination_blob = archive_bucket.copy_blob(
            original_blob,
            archive_bucket,
            new_name=archive_file_path
        )

        if destination_blob:
            print(f"Successfully archived to {destination_blob.name}")
            original_blob.delete()
            print(f"Successfully deleted original file: {original_blob.name}")
        else:
            print(f"Failed to archive {original_blob.name}")

    # Process each file found
    for file_info in files_to_process:
        print(f"\nProcessing file: {file_info['input_file']}")

        # Define the text to filter out from the Excel file
        specific_text = 'No filters applied'
        csv_from_excel(file_info, specific_text, date_columns, datetime_columns)

        # Archive the original file
        move_to_archive(file_info, archive_bucket)

        # Upload the processed output file to the landing bucket
        output_blob_path = os.path.join(output_file_path, file_info["output_name"])
        output_blob = landing_bucket.blob(output_blob_path)
        output_blob.upload_from_filename(file_info["output_file"])
        print(f"Uploaded processed file to: gs://{landing_bucket.name}/{output_blob.name}")

        # Clean up local temporary files
        os.remove(file_info["input_file"])
        os.remove(file_info["output_file"])
        print(f"Cleaned up temporary files for {os.path.basename(file_info['input_file'])}")

    print("\nAll files processed and moved successfully.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Process and clean CSV files from GCS.")
    parser.add_argument(
        "--landing_project",
        required=True,
        help="GCP project ID for the landing bucket"
    )
    parser.add_argument(
        "--staging_project",
        required=True,
        help="GCP project ID for the archive bucket"
    )
    parser.add_argument(
        "--landing_bucket",
        required=True,
        help="Name of the landing bucket"
    )
    parser.add_argument(
        "--archive_bucket",
        required=True,
        help="Name of the archive bucket"
    )
    parser.add_argument(
        "--file_name",
        required=True,
        help="Pattern to match the file name"
    )
    parser.add_argument(
        "--date_columns",
        required=False,
        default="",
        help=(
            "Comma-separated Excel column header name(s) to convert to date-only (YYYY-MM-DD) BigQuery format. "
            "All other columns are left untouched."
        )
    )
    parser.add_argument(
        "--datetime_columns",
        required=False,
        default="",
        help=(
            "Comma-separated Excel column header name(s) to convert to datetime (YYYY-MM-DD HH:MM:SS) BigQuery "
            "format. All other columns are left untouched."
        )
    )

    # Parse the arguments and run the main function
    args = parser.parse_args()
    main(args)
