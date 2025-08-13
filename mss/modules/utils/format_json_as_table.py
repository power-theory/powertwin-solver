def format_json_as_table(json_data):
  if json_data and isinstance(json_data, list) and isinstance(json_data[0], dict):
    headers = json_data[0].keys()

    # Create table headers
    header_row = f"{' | '.join(headers)}"
    separator_row = '-' * len(header_row)

    # Collect rows of data
    rows = [header_row, separator_row]
    for item in json_data:
      row = ' | '.join(str(item.get(h, '')) for h in headers)
      rows.append(row)

    # Return the formatted table as a string
    return '\n'.join(rows)
  else:
    return "Invalid or empty data to format."
