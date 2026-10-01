from unittest import TestCase

from onyx_desktop.client import Client
from onyx_desktop.presentation import public_status


class PublicErrorTests(TestCase):
    def test_transport_os_and_arbitrary_exceptions_stay_out_of_statuses(self):
        client=Client('.', 'unused', preview=True)
        for raw in ('<urlopen error [WinError 10061] Connection refused>',
                    'Traceback: C:\\private\\backend.py', 'unexpected provider dump'):
            client.receive('featureError',raw)
            client.receive('settingsError',raw)
            self.assertEqual(client.featureStatus,'Action could not be completed')
            self.assertEqual(client.settingsStatus,'Action could not be completed')
            self.assertIn(raw,client.diagnostics)

    def test_validation_stays_useful_but_export_location_is_secondary(self):
        self.assertEqual(public_status('Use #RRGGBB'),'Use #RRGGBB')
        self.assertEqual(public_status('Export: C:\\private\\chat.json'),'Chat exported')
        self.assertEqual(public_status('Saved'),'Saved')
