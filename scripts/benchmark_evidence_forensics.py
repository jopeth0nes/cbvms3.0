"""Repeatable forensic evaluation; never installs a production threshold."""
import argparse
from collections import Counter
import hashlib
import io
import json
from pathlib import Path
import statistics
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image, ImageDraw, ImageEnhance


def make_fixtures(directory):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    import numpy as np
    rng=np.random.default_rng(1943)
    yy,xx=np.mgrid[:192,:256]
    pixels=np.stack(((xx*3+yy)%256,(yy*2)%256,(xx+yy*3)%256),axis=-1)
    pixels=np.clip(pixels+rng.normal(0,3,pixels.shape),0,255).astype('uint8')
    base=Image.fromarray(pixels,'RGB')
    ImageDraw.Draw(base).rectangle((40,40,100,130),fill=(50,180,90))
    initial_jpeg=io.BytesIO();base.save(initial_jpeg,'JPEG',quality=95)
    with Image.open(io.BytesIO(initial_jpeg.getvalue())) as encoded:
        recompressed_source=encoded.copy()
    screenshot=Image.new('RGB',(280,232),'#263545')
    screenshot.paste(base.resize((240,180)),(20,32))
    ImageDraw.Draw(screenshot).text((20,10),'Synthetic viewer',fill='white')
    rows=[]
    def emit(name,image,label,category,fmt='PNG',mask=None,**kwargs):
        path=directory/(name+('.jpg' if fmt=='JPEG' else '.png'))
        image.save(path,format=fmt,**kwargs)
        row={'id':name,'path':path.name,'label':label,'category':category,'group':'synthetic_scene_1',
             'rights':'Generated procedural fixture; no student data','sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
        if mask:
            target=directory/(name+'_mask.png');mask.save(target);row['mask']=target.name
        rows.append(row)
    benign=[('original_png',base,'original_png'),('original_jpeg',base,'original_jpeg'),
            ('resized',base.resize((128,96)),'resize'),('crop',base.crop((20,10,220,180)),'crop'),
            ('rotation',base.transpose(Image.Transpose.ROTATE_90),'rotation'),
            ('metadata_stripped',base.copy(),'metadata_stripped'),('recompressed',recompressed_source,'recompression'),
            ('app_compressed',recompressed_source.resize((160,120)),'app_recompression'),('screenshot_simulation',screenshot,'screenshot_simulation')]
    for name,im,category in benign:
        emit(name,im,'benign',category,'JPEG' if category in ('original_jpeg','recompression','app_recompression') else 'PNG',**({'quality':65} if category in ('original_jpeg','recompression','app_recompression') else {}))
    manipulations=[]
    for kind in ('clone','removal','insertion','splice','background','local_color','overlay','metadata_rewritten'):
        im=base.copy();mask=Image.new('L',base.size,0);draw=ImageDraw.Draw(im);md=ImageDraw.Draw(mask)
        if kind=='clone': im.paste(base.crop((40,40,100,130)),(150,40));md.rectangle((150,40,209,129),fill=255)
        elif kind=='removal':draw.rectangle((40,40,100,130),fill=(40,80,120));md.rectangle((40,40,100,130),fill=255)
        elif kind=='insertion':draw.ellipse((130,60,205,130),fill='red');md.ellipse((130,60,205,130),fill=255)
        elif kind=='splice':im.paste(Image.fromarray(rng.integers(0,255,(80,80,3),dtype='uint8')),(130,50));md.rectangle((130,50,209,129),fill=255)
        elif kind=='background':draw.rectangle((0,0,255,30),fill='blue');md.rectangle((0,0,255,30),fill=255)
        elif kind=='local_color':im.paste(ImageEnhance.Color(base.crop((80,80,150,160))).enhance(0),(80,80));md.rectangle((80,80,149,159),fill=255)
        elif kind=='overlay':draw.text((30,20),'EDITED',fill='white');md.rectangle((30,20,90,35),fill=255)
        elif kind=='metadata_rewritten':
            # Metadata rewriting alone is benign processing for the pixel detector.
            exif=Image.Exif();exif[305]='Fixture Editor';exif[306]='2001:01:01 00:00:00'
            emit(kind,im,'benign',kind,'JPEG',exif=exif);continue
        manipulations.append((kind,im,mask))
    for kind,im,mask in manipulations:
        emit(kind,im,'manipulated',kind,mask=mask)
        emit(kind+'_jpeg65',im,'manipulated','recompression',fmt='JPEG',mask=mask,quality=65)
        emit(kind+'_resize',im.resize((128,96)),'manipulated','resize',mask=mask.resize((128,96),Image.Resampling.NEAREST))
    manifest={'version':1,'purpose':'Pipeline smoke fixtures, NOT representative precision validation',
              'missing':['real phone originals','real screenshots/recapture','AI inpainting','AI object replacement','fully AI synthetic','independent scenes/devices','representative prevalence','C2PA provenance fixtures'], 'items':rows}
    target=directory/'manifest.json';target.write_text(json.dumps(manifest,indent=2));return target


def metrics(rows):
    matrix={label:dict.fromkeys(('positive','negative','abstain'),0) for label in ('benign','manipulated')}
    for row in rows:matrix[row['label']][row['prediction']]+=1
    tp=matrix['manipulated']['positive'];fp=matrix['benign']['positive']
    tn=matrix['benign']['negative'];fn=matrix['manipulated']['negative']
    def ratio(n,d):return n/d if d else None
    return {'confusion_matrix':matrix,'decided_sample_count':tp+fp+tn+fn,
            'coverage':ratio(tp+fp+tn+fn,len(rows)),
            'false_positive_rate_decided':ratio(fp,fp+tn),'false_negative_rate_decided':ratio(fn,fn+tp),
            'precision_decided':ratio(tp,tp+fp),'recall_decided':ratio(tp,tp+fn),
            'manipulated_without_alert_rate':ratio(fn+matrix['manipulated']['abstain'],sum(matrix['manipulated'].values())),
            'interpretation':'Rates with suffix decided exclude abstentions; coverage and manipulated_without_alert_rate MUST accompany them.'}


def evaluate(manifest_path,threshold=None):
    from core.evidence_forensics import analyze_evidence
    from core.appeal_evidence import validate_evidence
    source=Path(manifest_path);manifest=json.loads(source.read_text());rows=[]
    for item in manifest['items']:
        started=time.monotonic();row={k:item[k] for k in ('id','label','category')};row.update(prediction='abstain',failure=None,model_failure=None)
        try:
            path=(source.parent/item['path']).resolve()
            if not path.is_relative_to(source.parent.resolve()):raise ValueError('fixture_path_escape')
            data=path.read_bytes()
            if hashlib.sha256(data).hexdigest()!=item['sha256']:raise ValueError('fixture_hash_mismatch')
            validate_evidence(path.name,'image',data)
            report=analyze_evidence(data,path.name)
            model=report.get('model',{})
            row.update(classification=report['classification'],model_status=model.get('status'),c2pa_status=report['provenance']['c2pa'].get('status'))
            score=model.get('raw_score');row['raw_score']=score
            if model.get('status')=='error':row['model_failure']=model.get('reason','model_error')
            # Review alerts may concern provenance, not pixel manipulation; don't conflate them.
            if threshold is not None and model.get('status')=='experimental' and score is not None:
                row['prediction']='positive' if score>=threshold else 'negative'
            if item.get('mask') and report.get('localization_png') and threshold is not None:
                import numpy as np
                target=(source.parent/item['mask']).resolve()
                if not target.is_relative_to(source.parent.resolve()):raise ValueError('mask_path_escape')
                with Image.open(target) as im:truth=np.asarray(im.convert('L'))>0
                with Image.open(io.BytesIO(report['localization_png'])) as im:pred=np.asarray(im.resize((truth.shape[1],truth.shape[0]),Image.Resampling.BILINEAR))/255>=threshold
                union=np.logical_or(pred,truth).sum();row['localization_iou']=float(np.logical_and(pred,truth).sum()/union) if union else None
        except Exception as exc:row['failure']=type(exc).__name__
        row['runtime_ms']=round((time.monotonic()-started)*1000,3);rows.append(row)
    return {'dataset_manifest_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
            'purpose':manifest.get('purpose'),'missing':manifest.get('missing',[]),
            'evaluation_threshold':threshold,'production_threshold':None,'metrics':metrics(rows),
            'by_category':{cat:metrics([r for r in rows if r['category']==cat]) for cat in sorted({r['category'] for r in rows})},
            'runtime_ms':{'total':round(sum(r['runtime_ms'] for r in rows),3),'median':statistics.median(r['runtime_ms'] for r in rows)},
            'pipeline_failures':sum(r['failure'] is not None for r in rows),'model_failures':sum(r['model_failure'] is not None for r in rows),
            'model_status_counts':dict(Counter(r.get('model_status','unavailable') for r in rows)),'items':rows}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--generate',type=Path);p.add_argument('--manifest',type=Path);p.add_argument('--output',type=Path,required=True);p.add_argument('--evaluation-threshold',type=float)
    args=p.parse_args()
    if args.evaluation_threshold is not None and not 0<=args.evaluation_threshold<=1:p.error('threshold must be between 0 and 1')
    path=make_fixtures(args.generate) if args.generate else args.manifest
    if not path:p.error('--generate or --manifest is required')
    report=evaluate(path,args.evaluation_threshold);args.output.write_text(json.dumps(report,indent=2));print(json.dumps({k:v for k,v in report.items() if k not in ('items','by_category')},indent=2))

if __name__=='__main__':main()
