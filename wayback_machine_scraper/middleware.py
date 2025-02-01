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

class UnhandledIgnoreRequest(IgnoreRequest):
    pass

class WaybackMachineMiddleware:
    cdx_url_template = ('https://web.archive.org/cdx/search/cdx?url={url}'
                        '&output=json&fl=timestamp,original,statuscode,digest')
    snapshot_url_template = 'https://web.archive.org/web/{timestamp}id_/{original}'
    robots_txt = 'https://web.archive.org/robots.txt'
    timestamp_format = '%Y%m%d%H%M%S'

    def __init__(self, crawler):
        self.crawler = crawler
        # Read the settings
        time_range = crawler.settings.get('WAYBACK_MACHINE_TIME_RANGE')
        if not time_range:
            raise NotConfigured("WAYBACK_MACHINE_TIME_RANGE not configured")
        self.set_time_range(time_range)

    def set_time_range(self, time_range):
        # Allow a single time to be passed instead of a range.
        if not isinstance(time_range, (tuple, list)):
            time_range = (time_range, time_range)

        def parse_time(time):
            if isinstance(time, (int, float, str)):
                time = int(time)
                if 10**8 < time < 10**13:
                    return time
                time_string = str(time)[::-1].zfill(14)[::-1]
                time = datetime.strptime(time_string, self.timestamp_format)
                time = time.replace(tzinfo=timezone.utc)
            return time.timestamp()

        self.time_range = [parse_time(time) for time in time_range]

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler)

    def process_request(self, request, spider):
        # Ignore robots.txt requests.
        if request.url == self.robots_txt:
            return
        # Let Wayback Machine requests pass through.
        if request.meta.get('wayback_machine_url'):
            return
        if request.meta.get('wayback_machine_cdx_request'):
            return
        # Otherwise, build a CDX request.
        return self.build_cdx_request(request)

    def process_response(self, request, response, spider):
        meta = request.meta
        # If this is a CDX request, parse its response and schedule snapshot requests.
        if meta.get('wayback_machine_cdx_request'):
            snapshot_requests = self.build_snapshot_requests(response, meta)
            if len(snapshot_requests) < 1:
                return Response(meta['wayback_machine_original_request'].url, status=404)
            for snapshot_request in snapshot_requests:
                try:
                    # Enqueue and schedule using the latest Scrapy API.
                    self.crawler.engine.slot.scheduler.enqueue_request(snapshot_request)
                    self.crawler.engine.crawl(snapshot_request, spider)
                    logger.debug("Scheduled snapshot request: %s", snapshot_request.url)
                except Exception as e:
                    logger.error("Error scheduling snapshot request: %s", e)
            raise UnhandledIgnoreRequest("Aborting original request in favor of snapshot requests")
        # For snapshot responses, restore the original URL.
        if meta.get('wayback_machine_url'):
            original_request = meta.get('wayback_machine_original_request')
            if original_request:
                return response.replace(url=original_request.url)
        return response

    def build_cdx_request(self, request):
        if os.name == 'nt':
            cdx_url = self.cdx_url_template.format(url=pathname2url(request.url.split('://')[1]))
        else:
            cdx_url = self.cdx_url_template.format(url=pathname2url(request.url))
        cdx_request = Request(cdx_url)
        cdx_request.meta['wayback_machine_original_request'] = request
        cdx_request.meta['wayback_machine_cdx_request'] = True
        return cdx_request

    def build_snapshot_requests(self, response, meta):
        assert meta.get('wayback_machine_cdx_request'), 'Not a CDX request meta.'

        try:
            data = json.loads(response.text)
        except json.decoder.JSONDecodeError:
            data = []
        if len(data) < 2:
            return []
        keys, rows = data[0], data[1:]
        def build_dict(row):
            new_dict = {}
            for i, key in enumerate(keys):
                if key == 'timestamp':
                    try:
                        time = datetime.strptime(row[i], self.timestamp_format)
                        new_dict['datetime'] = time.replace(tzinfo=timezone.utc)
                    except ValueError:
                        new_dict['datetime'] = None
                new_dict[key] = row[i]
            return new_dict
        snapshots = list(map(build_dict, rows))
        del rows

        snapshot_requests = []
        for snapshot in self.filter_snapshots(snapshots):
            url = self.snapshot_url_template.format(**snapshot)
            original_request = meta['wayback_machine_original_request']
            snapshot_request = original_request.replace(url=url)
            snapshot_request.meta.update({
                'wayback_machine_original_request': original_request,
                'wayback_machine_url': snapshot_request.url,
                'wayback_machine_time': snapshot['datetime'],
            })
            snapshot_requests.append(snapshot_request)
        return snapshot_requests

    def filter_snapshots(self, snapshots):
        filtered_snapshots = []
        initial_snapshot = None
        last_digest = None
        for snapshot in snapshots:
            if not snapshot['datetime']:
                continue
            timestamp = snapshot['datetime'].timestamp()
            if len(snapshot['statuscode']) != 3:
                continue
            if snapshot['statuscode'][0] == '3':
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
