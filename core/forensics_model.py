"""Optional, local TruFor evaluation. No production detection threshold exists."""
import hashlib
import io
import os
from pathlib import Path
import re
import sys

SOURCE_REVISION='ae54475df6f41a491d7615100feb19263dec13f7'
SOURCE_SHA256='6b06b819fac4b0095e37025793aa34be57532bcb15b8f5a205fc326f82203d9b'
MODEL_MAX_PIXELS=1024*1024

def file_hash(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024), b''): h.update(chunk)
    return h.hexdigest()

def source_hash(root):
    h=hashlib.sha256()
    for path in sorted(Path(root).rglob('*')):
        if path.is_file() and path.suffix in ('.py','.yaml','.txt'):
            h.update(path.relative_to(root).as_posix().encode()+b'\0'+path.read_bytes()+b'\0')
    return h.hexdigest()

def _png(array):
    import numpy as np
    from PIL import Image
    if array.ndim!=2 or not np.isfinite(array).all(): raise ValueError('invalid_map')
    im=Image.fromarray(np.uint8(np.clip(array,0,1)*255), mode='L')
    im.thumbnail((1024,1024));output=io.BytesIO();im.save(output,format='PNG',optimize=True)
    if output.tell()>512*1024: raise ValueError('map_too_large')
    return output.getvalue()

def inspect_model(data, image_format):
    base={'name':'TruFor','version':SOURCE_REVISION,'calibrated':False,'device':'cpu',
          'license':'GRIP-UNINA informational/nonprofit; includes CMX notices'}
    if os.environ.get('CBVMS_FORENSICS_ENABLE_EXPERIMENTAL')!='1':
        return dict(base,status='not_configured',reason='experimental_detector_disabled')
    if os.environ.get('CBVMS_FORENSICS_LICENSE_ACCEPTED')!='1':
        return dict(base,status='not_configured',reason='model_license_not_acknowledged')
    root=Path(os.environ.get('CBVMS_FORENSICS_TRUFOR_ROOT',''))
    weights=Path(os.environ.get('CBVMS_FORENSICS_TRUFOR_WEIGHTS',''))
    expected=os.environ.get('CBVMS_FORENSICS_TRUFOR_SHA256','')
    if not re.fullmatch('[0-9a-f]{64}',expected) or not root.is_dir() or not weights.is_file():
        return dict(base,status='not_configured',reason='verified_model_not_configured')
    try:
        if weights.stat().st_size>1024*1024*1024 or file_hash(weights)!=expected:
            raise ValueError('weights_checksum_mismatch')
        if source_hash(root)!=SOURCE_SHA256: raise ValueError('source_checksum_mismatch')
        from PIL import Image
        with Image.open(io.BytesIO(data)) as source:
            if source.width*source.height>MODEL_MAX_PIXELS or min(source.size)<32:
                return dict(base,status='error',reason='experimental_model_size_limit',weights_sha256=expected)
            # The evidence is never resized/re-encoded. Derived tensor uses upstream preprocessing.
            rgb=source.convert('RGB')
            original_size=source.size
            orientation=source.getexif().get(274,1)
        import numpy as np
        import torch
        if tuple(int(v) for v in torch.__version__.split('+')[0].split('.')[:2]) < (2,10):
            raise ValueError('isolated_torch_2_10_or_newer_required')
        from importlib.util import spec_from_file_location, module_from_spec
        sys.path.insert(0,str(root.resolve()))
        spec=spec_from_file_location('_cbvms_trufor_config',root/'config.py')
        config_module=module_from_spec(spec);spec.loader.exec_module(config_module)
        cfg=config_module._C.clone();cfg.defrost();cfg.merge_from_file(str(root/'trufor.yaml'));cfg.freeze()
        from models.cmx.builder_np_conf import myEncoderDecoder
        torch.set_num_threads(1);torch.set_num_interop_threads(1)
        torch.manual_seed(0)
        checkpoint=torch.load(weights,map_location='cpu',weights_only=True)
        model=myEncoderDecoder(cfg=cfg);model.load_state_dict(checkpoint['state_dict']);model.eval()
        tensor=torch.tensor(np.array(rgb).transpose(2,0,1),dtype=torch.float32).unsqueeze(0)/256.0
        with torch.inference_mode():
            pred,conf,det,_=model(tensor)
            localization=torch.softmax(pred.squeeze(0),dim=0)[1].cpu().numpy()
            reliability=torch.sigmoid(conf.squeeze(0))[0].cpu().numpy() if conf is not None else None
            score=float(torch.sigmoid(det).item()) if det is not None else None
        if score is not None and not np.isfinite(score): raise ValueError('invalid_score')
        return dict(base,status='experimental',weights_sha256=expected,source_sha256=SOURCE_SHA256,
                    raw_score=score,score_meaning='Uncalibrated detector output; not authenticity or misconduct probability.',
                    reliability={'calibrated':False,'summary':'Model self-estimate; not independently calibrated for CBVMS.'},
                    localization_png=_png(localization),reliability_png=_png(reliability) if reliability is not None else None,
                    map_coordinates='encoded_pixels_before_exif_orientation',image_size=list(original_size),orientation=orientation)
    except Exception as exc:
        return dict(base,status='error',reason='model_inference_unavailable',error_type=type(exc).__name__,weights_sha256=expected)
