import os
import json
from datetime import datetime, timezone
try:
    from urllib.request import pathname2url
except ImportError:
    from urllib import pathname2url

from scrapy import Request
from scrapy.http import Response
from scrapy.exceptions import NotConfigured
import logging

logger = logging.getLogger(__name__)

class WaybackMachineMiddleware:
    """Middleware to handle Wayback Machine requests and responses."""
    
    robots_txt = 'https://web.archive.org/robots.txt'
    timestamp_format = '%Y%m%d%H%M%S'
    snapshot_url_template = 'https://web.archive.org/web/{timestamp}id_/{original}'
    
    def __init__(self, crawler):
        self.crawler = crawler
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

    def process_request(self, request, spider):
        # Ignore robots.txt requests
        if request.url == self.robots_txt:
            return None

        # Let Wayback Machine requests pass through
        if request.meta.get('wayback_machine_url'):
            return None
        if request.meta.get('wayback_machine_cdx_request'):
            return None

        # Otherwise, request a CDX listing of available snapshots
        try:
            return self.build_cdx_request(request)
        except Exception as e:
            logger.error(f"Error building CDX request for {request.url}: {str(e)}")
            return None

    def process_response(self, request, response, spider):
        meta = request.meta

        # Handle error status codes
        if response.status >= 400 and not meta.get('wayback_machine_cdx_request'):
            logger.warning(f"Received {response.status} for {response.url}")
            if response.status == 404:
                return response
            if response.status >= 500:
                # Retry server errors
                retries = meta.get('retry_times', 0)
                if retries < 3:
                    meta['retry_times'] = retries + 1
                    new_request = request.copy()
                    new_request.dont_filter = True
                    new_request.meta.update(meta)
                    return new_request
            return response

        # Process CDX responses
        if meta.get('wayback_machine_cdx_request'):
            try:
                # Log the response for debugging
                logger.debug(f"CDX Response Text: {response.text[:500]}...")
                
                snapshot_requests = self.build_snapshot_requests(response, meta)
                if not snapshot_requests:
                    logger.info(f"No snapshots found for {meta['wayback_machine_original_request'].url}")
                    return Response(meta['wayback_machine_original_request'].url, status=404)
                
                # Add requests to scheduler
                for snapshot_request in snapshot_requests:
                    try:
                        self.crawler.engine.slot.scheduler.enqueue_request(snapshot_request)
                        logger.debug(f"Enqueued snapshot request: {snapshot_request.url}")
                    except Exception as e:
                        logger.error(f"Failed to enqueue request {snapshot_request.url}: {str(e)}")
                
                return Response(meta['wayback_machine_original_request'].url, status=200)
            except Exception as e:
                logger.error(f"Error processing CDX response: {str(e)}\nResponse text: {response.text[:500]}")
                return response

        # For snapshot responses, restore the original URL
        if meta.get('wayback_machine_url'):
            original_request = meta.get('wayback_machine_original_request')
            if original_request:
                return response.replace(url=original_request.url)

        return response

    def build_cdx_request(self, request):
        try:
            # Split URL into parts and ensure proper encoding
            url = request.url
            if '://' in url:
                # Remove port number if present and normalize the URL
                base_url = url.split('://', 1)[1].split(':', 1)[0].strip('/')
                if not base_url:
                    base_url = url
            else:
                base_url = url.strip('/')

            # Build CDX URL with proper encoding
            cdx_url = 'https://web.archive.org/cdx/search/cdx'
            cdx_url += '?url=' + pathname2url(base_url)
            cdx_url += '&output=json&fl=timestamp,original,statuscode,digest'

            logger.debug(f"Built CDX URL: {cdx_url}")
            
            cdx_request = Request(
                url=cdx_url,
                dont_filter=True,
                meta={
                    'wayback_machine_original_request': request,
                    'wayback_machine_cdx_request': True,
                    'handle_httpstatus_list': list(range(400, 600))  # Handle all error status codes
                }
            )
            return cdx_request
        except Exception as e:
            logger.error(f"Error building CDX request for {request.url}: {str(e)}")
            return None

    def build_snapshot_requests(self, response, meta):
        try:
            data = json.loads(response.text)
        except json.decoder.JSONDecodeError as e:
            logger.error(f"Invalid JSON in CDX response: {str(e)}")
            return []

        if len(data) < 2:
            logger.debug("No snapshot data found in CDX response")
            return []

        keys, rows = data[0], data[1:]
        
        def build_dict(row):
            new_dict = {}
            for i, key in enumerate(keys):
                if key == 'timestamp':
                    try:
                        time = datetime.strptime(row[i], self.timestamp_format)
                        new_dict['datetime'] = time.replace(tzinfo=timezone.utc)
                    except ValueError as e:
                        logger.error(f"Error parsing timestamp {row[i]}: {str(e)}")
                        new_dict['datetime'] = None
                new_dict[key] = row[i]
            return new_dict

        snapshots = list(map(build_dict, rows))
        filtered_snapshots = self.filter_snapshots(snapshots)
        snapshot_requests = []

        for snapshot in filtered_snapshots:
            try:
                url = self.snapshot_url_template.format(**snapshot)
                original_request = meta['wayback_machine_original_request']
                snapshot_request = original_request.replace(url=url, dont_filter=True)
                snapshot_request.meta.update({
                    'wayback_machine_original_request': original_request,
                    'wayback_machine_url': url,
                    'wayback_machine_time': snapshot['datetime'],
                    'handle_httpstatus_list': list(range(400, 600))  # Handle all error status codes
                })
                snapshot_requests.append(snapshot_request)
            except Exception as e:
                logger.error(f"Error building snapshot request: {str(e)}")
                continue

        return snapshot_requests

    def filter_snapshots(self, snapshots):
        filtered_snapshots = []
        initial_snapshot = None
        last_digest = None

        for snapshot in snapshots:
            if not snapshot['datetime']:
                continue
                
            timestamp = snapshot['datetime'].timestamp()
            
            # Skip entries with invalid status codes
            if not snapshot['statuscode'].isdigit():
                continue
            
            status_code = int(snapshot['statuscode'])
            # Skip redirect status codes and error codes
            if status_code >= 300:
                continue

            if not filtered_snapshots:
                if timestamp > self.time_range[0]:
                    if initial_snapshot:
                        filtered_snapshots.append(initial_snapshot)
                        last_digest = initial_snapshot['digest']
                else:
                    initial_snapshot = snapshot
                    
            if timestamp < self.time_range[0]:
                continue
                
            if timestamp > self.time_range[1]:
                break
                
            if last_digest == snapshot['digest']:
                continue
                
            last_digest = snapshot['digest']
            filtered_snapshots.append(snapshot)

        return filtered_snapshots