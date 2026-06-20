# Python Place Reviews Job

Standalone Google Maps place + reviews scraper.

This folder is intentionally independent from the existing projects. It does not
import or modify the old code.

## Input

Create a text file with one Google Maps place URL per line:

```txt
https://www.google.com/maps/place/...
https://maps.app.goo.gl/...
```

Blank lines and lines starting with `#` are ignored.

## Run

```powershell
py run_job.py --urls urls.txt --output output.json --headless --max-reviews 100
```

Verbose logging:

```powershell
py run_job.py --urls urls.txt --output output.json --headless --log-level DEBUG --log-file logs\scrape.log
```

Log levels:

- `INFO`: default, shows high-level progress.
- `DEBUG`: includes selector/card-level details.
- `WARNING` / `ERROR`: quieter modes.

If `py` is not available, use a direct Python executable:

```powershell
python run_job.py --urls urls.txt --output output.json --headless
```

## Output

The output file has this shape:

```json
{
  "generated_at": "2026-06-07T00:00:00+00:00",
  "count": 1,
  "places": [
    {
      "input_url": "...",
      "status": "ok",
      "place_id": "...",
      "title": "...",
      "category": "...",
      "address": "...",
      "phone": "...",
      "website": "...",
      "reviewRating": 4.5,
      "reviewCount": 120,
      "latitude": 10.0,
      "longitude": 106.0,
      "reviews": []
    }
  ]
}
```
