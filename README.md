# The Wayback Machine Scraper

The repository consists of a command-line utility `wayback-machine-scraper` that can be used to scrape or download website data as it appears in [archive.org](http://archive.org)'s [Wayback Machine](https://archive.org/web/).
It crawls through historical snapshots of a website and saves the snapshots to disk.
This can be useful when you're trying to scrape a site that has scraping measures that make direct scraping impossible or prohibitively slow.
It's also useful if you want to scrape a website as it appeared at some point in the past or to scrape information that changes over time.

This version includes the Wayback Machine middleware directly in the repository, eliminating the need for the separate `scrapy-wayback-machine` dependency. The middleware handles all of the interaction with archive.org and passes normal `response` objects to your [Scrapy](https://scrapy.org) spiders with archive timestamp information attached.

## Installation

The package can be installed using `pip`:

```bash
pip install wayback-machine-scraper
```

## Command-Line Interface

The usage information can be printed by running `wayback-machine-scraper -h`.

```
usage: wayback-machine-scraper [-h] [-o DIRECTORY] [-f TIMESTAMP]
                               [-t TIMESTAMP] [-a REGEX] [-d REGEX]
                               [-c CONCURRENCY] [-u] [-v]
                               DOMAIN [DOMAIN ...]

Mirror all Wayback Machine snapshots of one or more domains within a specified
time range.

positional arguments:
  DOMAIN                Specify the domain(s) to scrape. Can also be a full
                        URL to specify starting points for the crawler.

optional arguments:
  -h, --help            show this help message and exit
  -o DIRECTORY, --output DIRECTORY
                        Directory to save the mirrored snapshots.
                        (default: website)
  -f TIMESTAMP, --from TIMESTAMP
                        The timestamp for the beginning of the range to
                        scrape. Can either be YYYYmmdd, YYYYmmddHHMMSS, or a
                        Unix timestamp. (default: 10000101)
  -t TIMESTAMP, --to TIMESTAMP
                        The timestamp for the end of the range to scrape. Use
                        the same timestamp as `--from` to specify a single
                        point in time. (default: 30000101)
  -a REGEX, --allow REGEX
                        A regular expression that all scraped URLs must match.
                        (default: ())
  -d REGEX, --deny REGEX
                        A regular expression to exclude matched URLs.
                        (default: ())
  -c CONCURRENCY, --concurrency CONCURRENCY
                        Target concurrency for crawl requests. The crawl rate
                        will be automatically adjusted to match this target.
                        Use values less than 1 to be polite and higher values 
                        to scrape more quickly. (default: 10.0)
  -u, --unix            Save snapshots as `UNIX_TIMESTAMP.snapshot` instead of
                        the default `YYYYmmddHHMMSS.snapshot`. (default:
                        False)
  -v, --verbose         Turn on debug logging. (default: False)
```

## Examples

### A Single Page Over Time

One of the key advantages of `wayback-machine-scraper` is its ability to download all available [archive.org](https://archive.org) snapshots. This is particularly useful for analyzing how pages change over time.

For example, to get all snapshots of a specific webpage:

```bash
wayback-machine-scraper -a 'example.com/page$' example.com/page
```

This will create a directory structure containing all snapshots:

```
website/
└── example.com
    └── page
        ├── 20070221033032.snapshot
        ├── 20070226001637.snapshot
        └── etc.
```

### A Full Site Crawl at One Point In Time

To take a snapshot of an entire site at a specific point in time, use the same timestamp for both `--from` and `--to`:

```bash
wayback-machine-scraper -f 20230101 -t 20230101 example.com
```

This produces:
```
website/
└── example.com/
    ├── index.html/
    │   └── 20230101000000.snapshot
    ├── about/
    │   └── 20230101000000.snapshot
    └── etc.
```

## Advanced Usage

### Using the Middleware Directly

The Wayback Machine middleware is now included in this package and can be used directly in your Scrapy projects. Add it to your spider's settings:

```python
DOWNLOADER_MIDDLEWARES = {
    'wayback_machine_scraper.middleware.WaybackMachineMiddleware': 5,
}

# Configure the time range for snapshots
WAYBACK_MACHINE_TIME_RANGE = ('20230101', '20230630')  # From Jan 1 2023 to June 30 2023
```

### Debug Mode

For troubleshooting, use the verbose flag to see detailed logs:

```bash
wayback-machine-scraper -v example.com
```

This will show:
- CDX API requests and responses
- Snapshot filtering details
- Request scheduling information
- Any errors or warnings

### Error Handling

The scraper includes robust error handling for:
- Invalid timestamps
- Network issues
- Rate limiting
- Missing snapshots
- HTTP error codes

Failed requests are logged and can be retried using the built-in retry mechanism.