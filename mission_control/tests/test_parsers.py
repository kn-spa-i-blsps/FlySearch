
import unittest
from unittest.mock import patch, mock_open
import json
from mission_control.utils.parsers import (
    parse_telemetry,
    parse_prompt_arguments,
    parse_search_arguments,
    parse_xml_response,
    ModelResponse,
    ParsingError
)

class TestParsers(unittest.TestCase):

    @patch("builtins.open", new_callable=mock_open, read_data='{"data": {"position": {"alt": 25}}}')
    def test_parse_telemetry_success(self, mock_file):
        """Test successful parsing of telemetry data."""
        message, height = parse_telemetry('fake/path.json')
        self.assertEqual(height, 25)
        self.assertEqual(message, "Your current altitude is 25 meters above ground level.")
        mock_file.assert_called_with('fake/path.json', 'r', encoding='utf-8')

    @patch("builtins.open", new_callable=mock_open, read_data='{}')
    def test_parse_telemetry_missing_data(self, mock_file):
        """Test parsing of telemetry data with missing keys."""
        message, height = parse_telemetry('fake/path.json')
        self.assertEqual(height, 10) # Default value
        self.assertEqual(message, "Your current altitude is 10 meters above ground level.")

    @patch("builtins.open", side_effect=FileNotFoundError)
    def test_parse_telemetry_file_not_found(self, mock_file):
        """Test that FileNotFoundError is raised if the telemetry file does not exist."""
        with self.assertRaises(FileNotFoundError):
            parse_telemetry('non_existent_path.json')

    @patch("builtins.open", new_callable=mock_open, read_data='{invalid json')
    def test_parse_telemetry_invalid_json(self, mock_file):
        """Test that json.JSONDecodeError is raised for invalid JSON."""
        with self.assertRaises(json.JSONDecodeError):
            parse_telemetry('invalid_json.json')

    def test_parse_prompt_arguments_success(self):
        """Test successful parsing of prompt arguments."""
        kind, kv = parse_prompt_arguments("FS-1 object=helipad area=100 minimum_altitude=12")
        self.assertEqual(kind, "FS-1")
        self.assertEqual(kv, {"object": "helipad", "area": 100, "minimum_altitude": "12"})

    def test_parse_prompt_arguments_no_kv(self):
        """Test parsing prompt arguments with no key-value pairs."""
        kind, kv = parse_prompt_arguments("FS-2")
        self.assertEqual(kind, "FS-2")
        self.assertEqual(kv, {})

    def test_parse_prompt_arguments_invalid_kind(self):
        """Test that parsing prompt arguments with invalid kind raises ValueError."""
        with self.assertRaises(ValueError):
            parse_prompt_arguments("FS-3 object=test")

    def test_parse_prompt_arguments_empty(self):
        """Test that parsing empty prompt arguments raises ValueError."""
        with self.assertRaises(ValueError):
            parse_prompt_arguments("")

    def test_parse_search_arguments_success(self):
        """Test successful parsing of search arguments."""
        name, kind, kv = parse_search_arguments(
            "test_search FS-2 object=car glimpses=5 minimum_altitude=15"
        )
        self.assertEqual(name, "TEST_SEARCH")
        self.assertEqual(kind, "FS-2")
        self.assertEqual(kv, {"object": "car", "glimpses": 5, "minimum_altitude": "15"})

    def test_parse_search_arguments_invalid(self):
        """Test that parsing invalid search arguments raises ValueError."""
        with self.assertRaises(ValueError):
            parse_search_arguments("test_search") # Missing kind

    def test_parse_search_arguments_missing_glimpses(self):
        """Test that SEARCH requires a glimpses argument."""
        with self.assertRaises(ValueError):
            parse_search_arguments("test_search FS-1 object=helipad area=80")

    def test_parse_search_arguments_invalid_glimpses(self):
        """Test that non-integer glimpses raises ValueError."""
        with self.assertRaises(ValueError):
            parse_search_arguments("test_search FS-1 object=helipad glimpses=abc")

    def test_parse_xml_response_found(self):
        """Test parsing a 'found' response."""
        response = parse_xml_response("<action>found</action>")
        self.assertTrue(response.found)
        self.assertEqual(response.move, None)

    def test_parse_xml_response_move(self):
        """Test parsing a 'move' response."""
        response = parse_xml_response("<action>(1.2, -3.4, 5.6)</action>")
        self.assertFalse(response.found)
        self.assertEqual(response.move, (1.2, -3.4, 5.6))

    def test_parse_xml_response_flexible_formatting(self):
        """Test that XML parsing is flexible with whitespace, case, and other tags."""
        # Case-insensitivity
        response_case = parse_xml_response("<ACTION>FOUND</ACTION>")
        self.assertTrue(response_case.found)

        # Whitespace
        response_space = parse_xml_response("  <action>  (1, 2, 3)   </action>  ")
        self.assertEqual(response_space.move, (1.0, 2.0, 3.0))

        # Other XML tags
        response_other_tags = parse_xml_response("<response><thought>I should move.</thought><action>(-1, 0, 0)</action></response>")
        self.assertEqual(response_other_tags.move, (-1.0, 0.0, 0.0))

    def test_parse_xml_response_no_xml_but_found(self):
        """Test parsing a non-XML response that contains 'found'."""
        response = parse_xml_response("I have found the object.")
        self.assertTrue(response.found)

    def test_parse_xml_response_invalid_xml(self):
        """Test that invalid XML raises a ParsingError."""
        with self.assertRaises(ParsingError):
            parse_xml_response("some other text")

    def test_parse_xml_response_invalid_action(self):
        """Test that an invalid action format raises a ParsingError."""
        with self.assertRaises(ParsingError):
            parse_xml_response("<action>invalid_move</action>")

if __name__ == '__main__':
    unittest.main()
