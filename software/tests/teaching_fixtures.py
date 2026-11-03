"""Offline fixtures only. No CNN execution or new board acceptance."""
from copy import deepcopy
from hashlib import sha256
import json
from itertools import count

from dermademo.device.formal_session import FormalOutcome
from dermademo.formal_results import replay_pair
from dermademo.formal_input import prepare_image
from dermademo.resources import resource_path


def prepared_case(index=0):
    root = resource_path('full_image_root')
    row = json.loads((root/'samples.json').read_text('utf-8'))[index]
    return prepare_image(root/row['directory']/row['input_file'])


_frames = count(3000000001)


def offline_outcome(prepared, directory, frame=None):
    frame = next(_frames) if frame is None else frame
    report = deepcopy(replay_pair()[1].metadata['wire_report'])
    mask = None
    for key in ('full_image_root', 'roi_root'):
        for path in (resource_path(key)/'samples').glob('*/input.rgb888.bin'):
            if path.read_bytes() == prepared.rgb:
                mask = (path.parent/'mask.bin').read_bytes()
    assert mask is not None
    report.update(origin='protocol_simulator', board=['127.0.0.1', 5000], pc=['127.0.0.1', 6102],
                  input_context=prepared.context, input_sha256=sha256(prepared.rgb).hexdigest(),
                  mask_sha256=sha256(mask).hexdigest(), hardware_finish_confirmed=True,
                  local_artifacts_complete=True, frame_id=frame)
    from dermademo.device.formal_protocol import assets
    report['official_fixed_sample_exact'] = report['input_sha256'] == assets()[0]['sample_RGB_sha256']
    report['last_reply'].update(frame=frame, result_frame=frame)
    return FormalOutcome(prepared.rgb, mask, report, None, directory, prepared.source_bytes)
