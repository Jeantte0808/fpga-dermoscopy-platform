"""Test-only crop converter. NOT member2's algorithm or a production ROI adapter."""
import cv2
from dermademo.roi import crop_rgb, create_roi_input


class FixtureROIProcessor:
    def prepare(self, source, rect):
        crop = crop_rgb(source, rect)
        rgb = cv2.resize(crop, (256, 256), interpolation=cv2.INTER_AREA)
        return create_roi_input(source, rect, rgb, parameters={'fixture': 'resize_only_NOT_MODEL_RULE'},
            confirmation={'status': 'test_fixture', 'test_only': True, 'rule_id': 'OFFLINE_ROI_WIRING_TEST_V1',
                          'golden_manifest_sha256': '1'*64})
