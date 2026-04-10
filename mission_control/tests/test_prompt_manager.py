import unittest
from unittest.mock import MagicMock, patch, mock_open

from mission_control.prompt_generation.prompts import Prompts

from mission_control.prompt_helpers.prompt_helper import FlySearchPromptHelper


class TestPromptManager(unittest.TestCase):

    def setUp(self):
        self.mock_config = MagicMock()
        self.mock_config.prompts_dir = '/fake/prompts/dir'
        self.mock_mission_context = MagicMock()
        self.prompt_manager = FlySearchPromptHelper(self.mock_config, self.mock_mission_context)

    def test_generate_fs1_prompt(self):
        """Test that FS-1 prompt is generated correctly."""
        kv = {'object': 'landing pad', 'glimpses': '5', 'area': '100', 'minimum_altitude': '15'}
        prompt_meta = self.prompt_manager._generate_prompt('FS-1', kv)
        self.assertEqual(prompt_meta['kind'], 'FS-1')
        self.assertIn('landing pad', prompt_meta['text'])
        self.assertIn('5', prompt_meta['text'])
        self.assertIn('100', prompt_meta['text'])
        self.assertIn('below the altitude of 15', prompt_meta['text'])
        self.assertEqual(prompt_meta['object'], 'landing pad')
        self.assertEqual(prompt_meta['glimpses'], 5)
        self.assertEqual(prompt_meta['area'], 100)
        self.assertEqual(prompt_meta['minimum_altitude'], 15)

    def test_generate_fs2_prompt(self):
        """Test that FS-2 prompt is generated correctly."""
        kv = {'object': 'red car', 'glimpses': '10', 'minimum_altitude': '20'}
        prompt_meta = self.prompt_manager._generate_prompt('FS-2', kv)
        self.assertEqual(prompt_meta['kind'], 'FS-2')
        self.assertIn('red car', prompt_meta['text'])
        self.assertIn('10', prompt_meta['text'])
        self.assertIn('below the altitude of 20', prompt_meta['text'])
        self.assertEqual(prompt_meta['object'], 'red car')
        self.assertEqual(prompt_meta['glimpses'], 10)
        self.assertEqual(prompt_meta['minimum_altitude'], 20)

    def test_generate_prompt_with_defaults(self):
        """Test that prompt generation uses default values."""
        kv = {}
        prompt_meta = self.prompt_manager._generate_prompt('FS-1', kv)
        self.assertEqual(prompt_meta['object'], 'helipad')
        self.assertEqual(prompt_meta['glimpses'], 6)
        self.assertEqual(prompt_meta['area'], 80)
        self.assertEqual(prompt_meta['minimum_altitude'], 10)

    def test_generate_prompt_invalid_kind_raises_error(self):
        """Test that generating a prompt with an invalid kind raises an error."""
        with self.assertRaises(ValueError):
            self.prompt_manager._generate_prompt('invalid_kind', {})

    @patch('os.path.join', return_value='/fake/prompts/dir/fs-1_20230101_120000.txt')
    @patch('builtins.open', new_callable=mock_open)
    @patch('json.dump')
    @patch('mission_control.managers.prompt_manager.datetime')
    def test_save_prompt(self, mock_datetime, mock_json_dump, mock_file_open, mock_path_join):
        """Test that prompt is saved correctly."""
        mock_now = MagicMock()
        mock_now.strftime.return_value = "20230101_120000"
        mock_datetime.now.return_value = mock_now

        prompt_meta = {
            'kind': 'FS-1',
            'text': 'This is a test prompt.',
            'object': 'test_object',
            'glimpses': 3,
            'area': 50,
            'minimum_altitude': 10
        }

        saved_paths = self.prompt_manager._save_prompt(prompt_meta)

        # Check that the text file was written correctly
        mock_file_open.assert_any_call('/fake/prompts/dir/fs-1_20230101_120000.txt', 'w', encoding='utf-8')
        mock_file_open().write.assert_called_once_with('This is a test prompt.')

        # Check that the json file was written correctly
        json_path = '/fake/prompts/dir/fs-1_20230101_120000.json'
        mock_path_join.return_value = json_path
        self.prompt_manager._save_prompt(prompt_meta)  # Call again to get the json path
        mock_file_open.assert_any_call(json_path, 'w', encoding='utf-8')

        # Verify the content of the JSON file
        meta_to_save = {
            'kind': 'FS-1',
            'object': 'test_object',
            'glimpses': 3,
            'area': 50,
            'minimum_altitude': 10,
            'saved_at': '20230101_120000'
        }
        mock_json_dump.assert_called_with(meta_to_save, mock_file_open(), ensure_ascii=False, indent=2)

        # Check the mission context cache
        self.assertEqual(self.mock_mission_context.last_prompt_text_cache, 'This is a test prompt.')

    @patch('mission_control.managers.prompt_manager.datetime')
    @patch('builtins.open', new_callable=mock_open)
    def test_save_prompt_io_error_raises_error(self, mock_file_open, mock_datetime):
        """Test that _save_prompt raises an IOError on file write error."""
        mock_now = MagicMock()
        mock_now.strftime.return_value = "20230101_120000"
        mock_datetime.now.return_value = mock_now

        mock_file_open.side_effect = IOError("Disk full")

        prompt_meta = {'kind': 'FS-1', 'text': '...'}
        with self.assertRaises(IOError):
            self.prompt_manager._save_prompt(prompt_meta)

    @patch.object(FlySearchPromptHelper, '_generate_prompt')
    @patch.object(FlySearchPromptHelper, '_save_prompt')
    def test_generate_and_save(self, mock_save, mock_generate):
        """Test the main entrypoint method."""
        mock_generate.return_value = {'kind': 'FS-1', 'text': '...'}
        mock_save.return_value = {'txt': 'path.txt', 'json': 'path.json'}

        self.prompt_manager.generate_and_save('FS-1', {})

        mock_generate.assert_called_once_with('FS-1', {})
        mock_save.assert_called_once_with({'kind': 'FS-1', 'text': '...'})

    @patch('builtins.print')
    @patch.object(FlySearchPromptHelper, '_generate_prompt', side_effect=Exception("Generation failed"))
    @patch.object(FlySearchPromptHelper, '_save_prompt')
    def test_generate_and_save_exception_handling(self, mock_save, mock_generate, mock_print):
        """Test that generate_and_save handles exceptions gracefully."""
        self.prompt_manager.generate_and_save('FS-1', {})

        mock_generate.assert_called_once_with('FS-1', {})
        mock_print.assert_called_once_with("Error in _generate_prompt or _save_prompt: Generation failed")
        mock_save.assert_not_called()


if __name__ == '__main__':
    unittest.main()
