import unittest

from bs4 import BeautifulSoup

from test_ufcstats import extract_fight_metadata, extract_general_info


class TestUFCStatsHelpers(unittest.TestCase):
    def test_extract_general_info_from_sample_markup(self):
        html = """
        <div>
            <i class="b-fight-details__fight-title">Weight class</i>
            <div>Light Heavyweight</div>

            <i class="b-fight-details__label">Method:</i>
            KO/TKO

            <i class="b-fight-details__label">Round:</i>
            2

            <i class="b-fight-details__label">Time:</i>
            1:32

            <i class="b-fight-details__label">Time format:</i>
            3 Rounds

            <i class="b-fight-details__label">Referee:</i>
            Marc Goddard
        </div>
        """
        soup = BeautifulSoup(html, "html.parser")
        general_info = extract_general_info(soup)

        self.assertEqual(general_info["weight_class"], "Light Heavyweight")
        self.assertEqual(general_info["method"], "KO/TKO")
        self.assertEqual(general_info["round"], "2")
        self.assertEqual(general_info["time"], "1:32")
        self.assertEqual(general_info["time_format"], "3 Rounds")
        self.assertEqual(general_info["referee"], "Marc Goddard")

    def test_extract_fight_metadata(self):
        html = "<html><head><title>UFC Fight Details</title></head><body></body></html>"
        soup = BeautifulSoup(html, "html.parser")
        metadata = extract_fight_metadata(soup)

        self.assertEqual(metadata["page_title"], "UFC Fight Details")
        self.assertEqual(metadata["source"], "ufcstats")


if __name__ == "__main__":
    unittest.main()
