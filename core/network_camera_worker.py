"""Private FFmpeg process. Config arrives on stdin; stdout is a framed pixel pipe."""
import json
import os
import queue
import struct
import sys
import threading
import time

MAX_FRAME_BYTES = 4096 * 2160 * 3


def main():
    request = json.loads(sys.stdin.buffer.readline(65536))
    # EOF also arrives if the GUI exits before its daemon owner finishes cleanup.
    # Do not leave an orphan decoder blocked in native open/read/release.
    def parent_closed():
        sys.stdin.buffer.read(1)
        os._exit(0)
    threading.Thread(target=parent_closed, daemon=True, name='parent-lifetime').start()
    output = os.fdopen(os.dup(sys.stdout.fileno()), 'wb')
    # Native messages go only to the parent's private classification pipe. They
    # must never corrupt pixel framing or reach the console/application log.
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    os.environ['OPENCV_FFMPEG_DEBUG'] = '1'
    os.environ['OPENCV_FFMPEG_LOGLEVEL'] = '24'
    # Preserve normal probing/reference-frame decoding. Dropping compressed input
    # during startup can strand HEVC decoding without its reference pictures.
    # Freshness is enforced by the latest-only queue *after* decoding instead.
    # discardcorrupt rejects packets flagged corrupt; it is not an image-quality
    # guarantee and OpenCV does not expose a reliable per-frame corruption flag.
    os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = (
        f"rtsp_transport;{request.get('transport','tcp')}|fflags;discardcorrupt")
    os.environ['OMP_NUM_THREADS'] = '1'
    import cv2
    cv2.setNumThreads(1)
    latest = queue.Queue(maxsize=1)

    def emit(header, pixels=b''):
        raw = json.dumps(header,separators=(',',':')).encode()
        output.write(struct.pack('!I',len(raw))+raw)
        if pixels: output.write(pixels)
        output.flush()

    def writer():
        while True:
            item=latest.get()
            if item is None: return
            header, frame=item
            try: emit(header, frame.tobytes())
            except (BrokenPipeError,OSError): os._exit(0)

    def offer(item):
        try: latest.put_nowait(item)
        except queue.Full:
            try: latest.get_nowait()
            except queue.Empty: pass
            latest.put_nowait(item)

    params=[cv2.CAP_PROP_OPEN_TIMEOUT_MSEC,request.get('open_timeout_ms',5000),
            cv2.CAP_PROP_READ_TIMEOUT_MSEC,request.get('read_timeout_ms',2500)]
    cap=None
    try:
        cap=cv2.VideoCapture(request['url'],cv2.CAP_FFMPEG,params)
        if not cap.isOpened():
            emit({'kind':'error','code':'open_failed'});return
        codec=int(cap.get(cv2.CAP_PROP_FOURCC))
        settings={'backend':cap.getBackendName(), 'width':cap.get(cv2.CAP_PROP_FRAME_WIDTH),
                  'height':cap.get(cv2.CAP_PROP_FRAME_HEIGHT), 'fps':cap.get(cv2.CAP_PROP_FPS),
                  'codec':''.join(chr((codec >> 8*i)&255) for i in range(4)).strip('\x00')}
        emit({'kind':'opened','settings':settings})
        threading.Thread(target=writer,daemon=True,name='pixel-pipe').start()
        sequence=0
        while True:
            ok,frame=cap.read()
            if not ok or frame is None or not frame.size:
                time.sleep(.01);continue  # Parent enforces first-frame/stall deadlines.
            if frame.ndim!=3 or frame.shape[2]!=3 or frame.nbytes>MAX_FRAME_BYTES:
                return
            sequence+=1
            offer(({'kind':'frame','shape':list(frame.shape),'sequence':sequence,
                    'captured_at':time.monotonic(),'wall_time':time.time()},frame))
    except Exception:
        # Do not expose arbitrary backend exceptions containing credentials.
        pass
    finally:
        if cap is not None: cap.release()


if __name__=='__main__':
    main()
