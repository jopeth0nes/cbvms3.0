"""Bounded descriptive measurements; no decision thresholds or confidence scores."""
import io
from PIL import Image

def describe_pixels(data):
    import numpy as np
    with Image.open(io.BytesIO(data)) as im:
        width,height=im.size
        # Preserve encoded coordinates and native resolution; no recompression/resizing.
        box=((width-min(width,1024))//2,(height-min(height,1024))//2,
             (width+min(width,1024))//2,(height+min(height,1024))//2)
        grey=np.asarray(im.crop(box).convert('L'),dtype=np.float32)
    facts={'sample_box_encoded_pixels':list(box),'sample_only':True,
           'interpretation':'Image content, camera processing, JPEG and resizing can all change these measurements. No manipulation threshold is applied.'}
    if min(grey.shape)<32:return facts
    dx=np.abs(np.diff(grey,axis=1));dy=np.abs(np.diff(grey,axis=0))
    facts['horizontal_difference_by_8px_phase']=[round(float(dx[:,phase::8].mean()),4) for phase in range(8)]
    facts['vertical_difference_by_8px_phase']=[round(float(dy[phase::8,:].mean()),4) for phase in range(8)]
    residual=grey[1:-1,1:-1]-(grey[:-2,1:-1]+grey[2:,1:-1]+grey[1:-1,:-2]+grey[1:-1,2:])/4
    # Texture/edge residual, deliberately not called a sensor-noise estimate.
    grid=[]
    for row in np.array_split(residual,4,axis=0):
        grid.append([round(float(np.median(np.abs(tile-np.median(tile)))),4) for tile in np.array_split(row,4,axis=1)])
    facts['local_highpass_mad_4x4']=grid
    return facts
