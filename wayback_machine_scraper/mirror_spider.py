import os
from datetime import datetime

try:
    from urllib.parse import quote_plus
except ImportError:
    from urllib import quote_plus

from scrapy.spiders import CrawlSpider, Rule
from scrapy.linkextractors import LinkExtractor

from scrapy_wayback_machine import WaybackMachineMiddleware


class MirrorSpider(CrawlSpider):
    name = 'mirror_spider'
    handle_httpstatus_list = [404]

    def __init__(self, domains, directory, allow=(), deny=(), unix=False, **kwargs):
        self.directory = directory
        self.unix = unix
        self.rules = (
            Rule(LinkExtractor(allow=allow, deny=deny), callback='save_page'),
        )

        # initialize allowed_domains list
        self.allowed_domains = []

        # if a domain is explicitly passed via kwargs, add it
        if 'domain' in kwargs:
            self.allowed_domains.append(kwargs['domain'])

        # Add common archive domains unconditionally
        archive_domains = ['archive.org', 'web.archive.org', 'wayback.archive.org']
        for domain in archive_domains:
            if domain not in self.allowed_domains:
                self.allowed_domains.append(domain)

        self.start_urls = []
        for domain in domains:
            # Expect domain to be something like "https://live.sysinternals.com"
            url_parts = domain.split('://')
            unqualified_url = url_parts[-1]
            url_scheme = url_parts[0] if len(url_parts) > 1 else 'http'
            full_url = '{0}://{1}'.format(url_scheme, unqualified_url)
            bare_domain = unqualified_url.split('/')[0]
            if bare_domain not in self.allowed_domains:
                self.allowed_domains.append(bare_domain)
            self.start_urls.append(full_url)

        super().__init__()

    def parse_start_url(self, response):
        # Scrapy doesn't call callbacks for start URLs by default,
        # so we iterate over rules and manually invoke callbacks if the link is allowed.
        for rule in self._rules:
            if rule.link_extractor._link_allowed(response):
                if rule.callback:
                    rule.callback(response)

    def save_page(self, response):
        # Ignore 404s
        if response.status == 404:
            return

        # Create a directory structure based on the URL parts
        url_parts = response.url.split('://')[1].split('/')
        if os.name == 'nt':
            url_parts = [quote_plus(url_part) for url_part in url_parts]
        parent_directory = os.path.join(self.directory, *url_parts)
        os.makedirs(parent_directory, exist_ok=True)

        # Construct the output filename based on the snapshot time
        time = response.meta['wayback_machine_time']
        if self.unix:
            filename = '{0}.snapshot'.format(time.timestamp())
        else:
            filename = '{0}.snapshot'.format(time.strftime(WaybackMachineMiddleware.timestamp_format))
        full_path = os.path.join(parent_directory, filename)

        # Write the snapshot to disk
        with open(full_path, 'wb') as f:
            f.write(response.body)
