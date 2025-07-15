# Google Sheets Setup Guide

## Overview

Vietnam Hearts uses Google Sheets for volunteer management and scheduling. The system can now accept either full Google Sheets URLs or just the sheet IDs for easier configuration.

## Configuration

### Setting Up Google Sheets URLs

In the admin dashboard settings, you can now configure Google Sheets using either:

1. **Full URL** (Recommended): `https://docs.google.com/spreadsheets/d/YOUR_SHEET_ID/edit`
2. **Sheet ID only**: `YOUR_SHEET_ID`

### Required Settings

1. **SCHEDULE_SHEETS_LINK**: The Google Sheets URL for the schedule spreadsheet
2. **NEW_SIGNUPS_RESPONSES_LINK**: The Google Sheets URL for new volunteer signups

### How to Find Your Sheet ID

1. Open your Google Sheet in the browser
2. Copy the URL from the address bar
3. The URL format is: `https://docs.google.com/spreadsheets/d/SHEET_ID/edit`
4. You can paste the entire URL or just extract the SHEET_ID part

### Examples

**Full URL (Recommended):**
```
https://docs.google.com/spreadsheets/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms/edit
```

**Sheet ID only:**
```
1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms
```

## Features

### Real-time Validation

The admin dashboard now includes real-time validation for Google Sheets URLs:
- ✅ Green border for valid URLs
- ❌ Red border for invalid URLs
- Tooltip showing the extracted sheet ID
- Helpful placeholder text with example URLs

### Backward Compatibility

The system maintains backward compatibility:
- Existing sheet ID configurations continue to work
- New URL-based configurations are automatically converted
- Both formats are supported simultaneously

## Troubleshooting

### Common Issues

1. **Invalid URL Format**: Make sure you're using a Google Sheets URL, not a Google Docs URL
2. **Permission Issues**: Ensure the service account has access to the sheets
3. **Sheet Not Found**: Verify the sheet ID is correct and the sheet exists

### Validation

The system validates URLs using the pattern:
```
https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit
```

Where `{SHEET_ID}` can contain:
- Letters (a-z, A-Z)
- Numbers (0-9)
- Hyphens (-)
- Underscores (_)

## Migration

If you have existing sheet ID configurations, they will continue to work without any changes. The new URL format is optional and provides a better user experience for new configurations. 