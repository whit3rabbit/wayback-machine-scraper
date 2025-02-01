import os
import json
from datetime import datetime, timezone
try:
    from urllib.request import pathname2url
except ImportError:
    from urllib import pathname2url

from scrapy import Request
from scrapy.http import Response
from scrapy.exceptions import NotConfigured, IgnoreRequest
import logging

logger = logging.getLogger(__name__)

class WaybackMachineMiddleware:
    """Middleware to handle Wayback Machine requests and responses."""
    
    robots_txt = 'https://web.archive.org/robots.txt'
    timestamp_format = '%Y%m%d%H%M%S'
    snapshot_url_template = 'https://web.archive.org/web/{timestamp}id_/{original}'
    
    def __init__(self, crawler):
        self.crawler = crawler
        self.logger = logging.getLogger(self.__class__.__name__)
        time_range = crawler.settings.get('WAYBACK_MACHINE_TIME_RANGE')
        if not time_range:
            raise NotConfigured("WAYBACK_MACHINE_TIME_RANGE not configured")
        self.set_time_range(time_range)

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler)

    def set_time_range(self, time_range):
        if not isinstance(time_range, (tuple, list)):
            time_range = (time_range, time_range)

        def parse_time(time):
            try:
                if isinstance(time, (int, float, str)):
                    time = int(str(time))
                    if 10**8 < time < 10**13:
                        return time
                    time_string = str(time)[::-1].zfill(14)[::-1]
                    time = datetime.strptime(time_string, self.timestamp_format)
                    time = time.replace(tzinfo=timezone.utc)
                return time.timestamp()
            except (ValueError, TypeError) as e:
                logger.error(f"Error parsing time {time}: {str(e)}")
                return None

        parsed_times = [parse_time(time) for time in time_range]
        if None in parsed_times:
            raise NotConfigured("Invalid time range format")
        self.time_range = parsed_times
        logger.debug(f"Set time range to: {self.time_range}")

    def process_request(self, request, spider):
        logger.debug(f"Processing request: {request.url}")
        
        # Ignore robots.txt requests
        if request.url == self.robots_txt:
            logger.debug("Ignoring robots.txt request")
            return None

        # Let Wayback Machine requests pass through
        if request.meta.get('wayback_machine_url'):
            logger.debug("Letting wayback machine request pass through")
            return None
        if request.meta.get('wayback_machine_cdx_request'):
            logger.debug("Letting CDX request pass through")
            return None

        # Build CDX request
        try:
            cdx_request = self.build_cdx_request(request)
            if cdx_request:
                logger.debug(f"Built CDX request: {cdx_request.url}")
                return cdx_request
            return None
        except Exception as e:
            logger.error(f"Error building CDX request for {request.url}: {str(e)}")
            return None

    def process_response(self, request, response, spider):
        logger.debug(f"Processing response: {response.url} (status: {response.status})")
        meta = request.meta

        # Process CDX responses
        if meta.get('wayback_machine_cdx_request'):
            try:
                logger.debug(f"Processing CDX response: {response.text[:500]}...")
                
                snapshot_requests = self.build_snapshot_requests(response, meta)
                if not snapshot_requests:
                    logger.info(f"No snapshots found for {meta['wayback_machine_original_request'].url}")
                    return Response(meta['wayback_machine_original_request'].url, status=404)
                
                logger.debug(f"Found {len(snapshot_requests)} snapshots")
                
                # Add requests to scheduler
                for snapshot_request in snapshot_requests:
                    try:
                        self.crawler.engine.crawl(snapshot_request)
                        logger.debug(f"Enqueued snapshot request: {snapshot_request.url}")
                    except Exception as e:
                        logger.error(f"Failed to enqueue request {snapshot_request.url}: {str(e)}")
                
                return Response(meta['wayback_machine_original_request'].url, status=200)
            except Exception as e:
                logger.error(f"Error processing CDX response: {str(e)}\nResponse text: {response.text[:500]}")
                return response

        # For snapshot responses, restore the original URL
        if meta.get('wayback_machine_url'):
            logger.debug("Processing wayback machine response")
            original_request = meta.get('wayback_machine_original_request')
            if original_request:
                return response.replace(url=original_request.url)

        return response

    def build_cdx_request(self, request):
        try:
            # Remove scheme and query parameters for CDX URL
            url = request.url
            if '://' in url:
                url = url.split('://', 1)[1]
            url = url.split('?')[0].split('#')[0]  # Remove query params and fragments
            
            # Ensure no trailing slash
            url = url.rstrip('/')
            
            # Build CDX URL
            cdx_url = 'https://web.archive.org/cdx/search/cdx'
            cdx_url += '?url=' + pathname2url(url)
            cdx_url += '&output=json&fl=timestamp,original,statuscode,digest'
            
            logger.debug(f"Built CDX URL: {cdx_url}")
            
            # Create request with metadata
            cdx_request = Request(
                url=cdx_url,
                callback=None,  # Let Scrapy handle it
                dont_filter=True,
                meta={
                    'wayback_machine_original_request': request,
                    'wayback_machine_cdx_request': True,
                    'handle_httpstatus_list': list(range(400, 600))
                }
            )
            return cdx_request
        except Exception as e:
            logger.error(f"Error building CDX request for {request.url}: {str(e)}")
            return None

    def build_snapshot_requests(self, response, meta):
        try:
            data = json.loads(response.text)
            if not data or len(data) < 2:
                logger.debug("No data in CDX response")
                return []

            keys, rows = data[0], data[1:]
            logger.debug(f"Found {len(rows)} CDX entries")

            snapshots = []
            for row in rows:
                snapshot = {}
                for i, key in enumerate(keys):
                    if key == 'timestamp':
                        try:
                            time = datetime.strptime(row[i], self.timestamp_format)
                            snapshot['datetime'] = time.replace(tzinfo=timezone.utc)
                        except ValueError:
                            snapshot['datetime'] = None
                    snapshot[key] = row[i]
                if snapshot['datetime']:  # Only add if timestamp parsed successfully
                    snapshots.append(snapshot)

            filtered_snapshots = self.filter_snapshots(snapshots)
            logger.debug(f"Filtered to {len(filtered_snapshots)} snapshots")

            snapshot_requests = []
            original_request = meta['wayback_machine_original_request']

            for snapshot in filtered_snapshots:
                url = self.snapshot_url_template.format(
                    timestamp=snapshot['timestamp'],
                    original=snapshot['original']
                )
                snapshot_request = original_request.replace(
                    url=url,
                    dont_filter=True,
                    meta={
                        'wayback_machine_original_request': original_request,
                        'wayback_machine_url': url,
                        'wayback_machine_time': snapshot['datetime'],
                        'handle_httpstatus_list': list(range(400, 600))
                    }
                )
                snapshot_requests.append(snapshot_request)

            logger.debug(f"Created {len(snapshot_requests)} snapshot requests")
            return snapshot_requests

        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error in CDX response: {str(e)}")
            return []
        except Exception as e:
            logger.error(f"Error building snapshot requests: {str(e)}")
            return []