from html.parser import HTMLParser

from research import research_dashboard as dashboard


class DisclosureParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.details = []
        self.owners = {}
        self.disclosures = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'details':
            self.details.append(attrs.get('id'))
            self.disclosures[attrs.get('id')] = attrs
        if attrs.get('id'):
            self.owners[attrs['id']] = tuple(self.details)

    def handle_endtag(self, tag):
        if tag == 'details':
            self.details.pop()


def test_overview_keeps_strategy_tiers_visible_and_receipts_accessible():
    parser = DisclosureParser()
    parser.feed(dashboard.DASHBOARD_HTML)
    for receipt in ('kpis', 'lifecycle-bundle-kpis', 'lifecycle-bundle-note', 'cohort-note'):
        assert parser.owners[receipt] == ('overview-evidence-details',)
    assert parser.owners['decision-readiness'] == ('overview-candidate-details',)
    for receipt in ('qualification-gate-body', 'decision-readiness-provenance'):
        assert parser.owners[receipt] == ('overview-qualification-details',)
    for visible in ('collection-status', 'strategy-leader-tiers', 'exec-text'):
        assert parser.owners[visible] == ()
    for name in ('overview-evidence-details', 'overview-candidate-details', 'overview-qualification-details'):
        assert 'open' not in parser.disclosures[name]
    assert not parser.details
