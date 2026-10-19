"""Windows webcam fallback and discovery regressions."""
import unittest
from unittest.mock import Mock, patch

import cv2

from api.camera_manager import scan_usb_cameras
from core.camera import CameraCapture, usb_camera_label


class USBCameraTests(unittest.TestCase):
    @patch('core.camera.platform.system', return_value='Windows')
    def test_windows_falls_back_after_directshow_failure(self, _system):
        capture = CameraCapture(camera_index=2)
        working = Mock()
        with patch.object(capture, '_open_index_backend', side_effect=[None, working]) as opening:
            self.assertIs(capture._try_open_index(2), working)
        self.assertEqual(opening.call_args_list[0].args, (2, cv2.CAP_DSHOW))
        self.assertEqual(opening.call_args_list[1].args, (2, cv2.CAP_MSMF))

    @patch('core.camera.platform.system', return_value='Windows')
    def test_windows_labels_camera_zero_as_usb(self, _system):
        self.assertEqual(usb_camera_label(0), 'USB Camera 0')

    @patch('api.camera_manager._USB_SCAN_MAX', 2)
    @patch('api.camera_manager.CameraCapture')
    def test_discovery_only_lists_cameras_with_frames_and_releases_handles(self, factory):
        unavailable, working = Mock(), Mock()
        unavailable.open.return_value = False
        working.open.return_value = True
        factory.side_effect = [unavailable, working]
        cameras = scan_usb_cameras()
        self.assertEqual([cam['index'] for cam in cameras], [1])
        unavailable.release.assert_called_once()
        working.release.assert_called_once()
