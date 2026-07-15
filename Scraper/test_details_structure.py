import unittest

from bs4 import BeautifulSoup

from test_ufcstats import extract_general_info


class TestDetailsParsing(unittest.TestCase):
    def test_decision_details_are_parsed_as_judges(self):
        html = """
        <div class="b-fight-details__fight">
            <i class="b-fight-details__fight-title">Light Heavyweight</i>
            <i class="b-fight-details__label">Method:</i> Decision
            <i class="b-fight-details__label">Round:</i> 3
            <i class="b-fight-details__label">Time:</i> 5:00
            <i class="b-fight-details__label">Time format:</i> 5 Rounds
            <i class="b-fight-details__label">Referee:</i> Marc Goddard
            <i class="b-fight-details__label">Details:</i>
            <i class="b-fight-details__text-item"><span>Ben Cartlidge</span> 28 - 29.</i>
            <i class="b-fight-details__text-item"><span>Anders Ohlsson</span> 28 - 29.</i>
        </div>
        """
        soup = BeautifulSoup(html, "html.parser")
        general_info = extract_general_info(soup)

        self.assertEqual(general_info["judges"][0]["judge"], "Ben Cartlidge")
        self.assertEqual(general_info["judges"][0]["score"], "28 - 29.")
        self.assertIsNone(general_info["finish_details"])

    def test_stoppage_details_are_parsed_as_finish_details(self):
        html = """
        <div class="b-fight-details__fight">
            <i class="b-fight-details__fight-title">Welterweight</i>
            <i class="b-fight-details__label">Method:</i> KO/TKO
            <i class="b-fight-details__label">Round:</i> 2
            <i class="b-fight-details__label">Time:</i> 1:32
            <i class="b-fight-details__label">Time format:</i> 3 Rounds
            <i class="b-fight-details__label">Referee:</i> Herb Dean
            <i class="b-fight-details__label">Details:</i>
            <i class="b-fight-details__text-item_first">Punches to Head On Ground</i>
        </div>
        """
        soup = BeautifulSoup(html, "html.parser")
        general_info = extract_general_info(soup)

        self.assertEqual(general_info["finish_details"], "Punches to Head On Ground")
        self.assertEqual(general_info["judges"], [])


if __name__ == "__main__":
    unittest.main()
