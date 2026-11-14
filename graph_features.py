"""PAPIMO daily graph candidates. OCR/CV never implies Work visual confirmation."""
import csv
import hashlib
import io
import itertools
import math
import re
import subprocess
from datetime import datetime,timedelta
import numpy as np
from PIL import Image

GRAPH_METHOD_VERSION = 'PAPIMO480-redcurve-axisOCR-v1.2.0; automatic estimates are not visual confirmation'


def read_tick(image, y):
    # Grid lines are lighter than label text. Keep the complete label at native size.
    strip = image.crop((0, max(0, y-9), 58, min(image.height, y+10)))
    strip = strip.convert('L').point(lambda p: 0 if p < 150 else 255).resize((348, 114))
    stream = io.BytesIO()
    strip.save(stream, format='PNG')
    result = subprocess.run(['tesseract', 'stdin', 'stdout', '--psm', '7', '-c',
                             'tessedit_char_whitelist=0123456789,-'], input=stream.getvalue(),
                            capture_output=True, check=True, timeout=15)
    value = result.stdout.decode().strip().replace(',', '')
    return int(value) if re.fullmatch(r'-?\d+', value) else None


def extract_candidates(raw):
    image = Image.open(io.BytesIO(raw)).convert('RGB')
    pixels = np.asarray(image).astype(int)
    height, width = pixels.shape[:2]
    if (width, height) != (480, 480):
        raise ValueError('unsupported_graph_layout: expected 480x480; requires visual review')
    # These layout bounds are specific to the verified PAPIMO 480px daily plot.
    gray = ((pixels.max(2)-pixels.min(2)) < 5) & (pixels[:,:,0] >= 185) & (pixels[:,:,0] <= 225)
    rows = np.where(gray[:, 55:470].sum(1) > 260)[0]
    tick_rows = []
    for y in rows:
        if 10 <= y < 450 and (not tick_rows or y-tick_rows[-1] > 3):
            tick_rows.append(int(y))
    labels = [(y, read_tick(image, y)) for y in tick_rows]
    labels = [(y, v) for y, v in labels if v is not None and v != 0]
    fits = []
    for (y1,v1),(y2,v2) in itertools.combinations(labels, 2):
        slope = (v2-v1)/(y2-y1)
        if slope >= 0 or abs(slope) < .1:
            continue
        offset = v1-slope*y1
        inliers = [(y,v) for y,v in labels if abs(slope*y+offset-v) <= max(5,abs(slope)*2)]
        fits.append((len(inliers), inliers))
    if not fits or max(f[0] for f in fits) < 3:
        raise ValueError('axis_not_calibrated: fewer than 3 consistent axis labels')
    inliers = max(fits, key=lambda f:f[0])[1]
    slope, offset = np.polyfit([p[0] for p in inliers], [p[1] for p in inliers], 1)
    zero_y = -offset/slope
    if not 10 <= zero_y <= 449:
        raise ValueError('zero_line_outside_plot')
    red = (pixels[:,:,0]>165) & (pixels[:,:,1]<110) & (pixels[:,:,2]<140)
    # PAPIMO curves can extend below the last grid line (e.g. y=452).
    # The grid boundary is not the image boundary and must not crop true extrema.
    red[:1,:] = False
    red[height-1:,:] = False
    red[:,:20] = False
    red[:,445:] = False
    points = [(x,float(np.median(np.where(red[:,x])[0]))) for x in range(20,445) if red[:,x].any()]
    if len(points) < 50 or points[0][0] > 30:
        raise ValueError('curve_missing_or_truncated')
    if abs(points[0][1]-zero_y) > 5:
        raise ValueError('curve_origin_disagrees_with_zero_line')
    values = [float(slope*y+offset) for _,y in points]
    high_index, low_index = int(np.argmax(values)), int(np.argmin(values))
    prefix_min, max_rise = values[0], 0
    for value in values:
        max_rise = max(max_rise, value-prefix_min)
        prefix_min = min(prefix_min, value)
    error = int(math.ceil(max(50,abs(slope)*3+25)/50)*50)
    unit = 50 if error <= 100 else 100
    rounded = lambda value: int(round(value/unit)*unit)
    final = rounded(values[-1])
    sign = 'positive' if values[-1]>error else 'negative' if values[-1]<-error else 'uncertain_near_zero'
    # Use the full red envelope for extrema: per-column medians can miss sharp vertices.
    curve_y=np.where(red)[0]
    envelope_high=float(slope*min(curve_y)+offset)
    envelope_low=float(slope*max(curve_y)+offset)
    return {'graph_sha256':hashlib.sha256(raw).hexdigest(), 'reading_status':'candidate_unreviewed',
            'difference_source':'image_candidate', 'candidate_signed_difference':final,
            'candidate_highest':max(0,rounded(envelope_high)), 'candidate_lowest':min(0,rounded(envelope_low)),
            'candidate_max_range':rounded(envelope_high-envelope_low),
            'candidate_max_chronological_rise':rounded(max_rise),
            'extrema_order':'high_then_low' if high_index<low_index else 'low_then_high',
            'rounding_unit':unit, 'estimated_error':error, 'sign_candidate':sign,
            'curve_clipped':bool(min(y for _,y in points)<=2 or max(y for _,y in points)>=height-2),
            'zero_line_y':round(float(zero_y),2), 'last_point':[points[-1][0],round(points[-1][1],2)],
            'axis_labels':[{'y':y,'value':v} for y,v in inliers],
            'coins_per_pixel':round(abs(float(slope)),6),
            'graph_updated_at_jst':None, 'graph_update_reason':'not_displayed; do_not_copy_page_update',
            'method':GRAPH_METHOD_VERSION}


def automatic_estimate(features):
    """Accept calibrated machine estimates without ever claiming a human review."""
    if features.get('reading_status')=='work_visual_confirmed':return dict(features)
    needed=['candidate_signed_difference','candidate_highest','candidate_lowest','estimated_error','rounding_unit']
    valid=(features.get('method')==GRAPH_METHOD_VERSION and not features.get('curve_clipped')
           and len(features.get('graph_sha256',''))==64 and len(features.get('axis_labels',[]))>=3
           and all(type(features.get(k)) in (int,float) and math.isfinite(features[k]) for k in needed))
    if not valid:return {**features,'automatic_check':'rejected; missing calibration, incompatible method, or clipped curve'}
    final,high,low=(features[k] for k in needed[:3])
    if not low<=final<=high or not low<=0<=high or features['estimated_error']<=0:
        return {**features,'automatic_check':'rejected; inconsistent numeric range'}
    return {**features,'signed_difference':final,'highest_difference':high,'lowest_difference':low,
            'max_range':high-low,'sign_judgment':features.get('sign_candidate','uncertain_near_zero'),
            'reading_status':'automatic_image_estimate','difference_source':'image_estimate_automatic',
            'automatic_check':'calibrated_axis_and_curve_verified','visual_confirmation':False}


def apply_review(features, review, machine, business_date, machine_code):
    required = ['signed_difference','highest_difference','lowest_difference','estimated_error',
                'rounding_unit','sign_judgment','zero_line_confirmed','axis_confirmed',
                'right_endpoint_confirmed','extrema_confirmed','reviewed_at_jst','reviewer']
    if any(key not in review for key in required):
        raise ValueError('review_missing_required_fields')
    for key, value in [('machine',machine),('business_date',business_date),('machine_code',machine_code),
                       ('graph_sha256',features.get('graph_sha256'))]:
        if review.get(key) != value:
            raise ValueError('review_identity_mismatch:'+key)
    if not all(review[k] is True for k in ['zero_line_confirmed','axis_confirmed','right_endpoint_confirmed','extrema_confirmed']):
        raise ValueError('review_not_confirmed')
    final, high, low = (review[k] for k in ['signed_difference','highest_difference','lowest_difference'])
    if not all(type(v) in (int,float) and math.isfinite(v) for v in [final,high,low]):
        raise ValueError('review_non_numeric_values')
    if not isinstance(review['reviewer'],str) or not review['reviewer'].strip():
        raise ValueError('reviewer_missing')
    try:
        checked=datetime.fromisoformat(review['reviewed_at_jst'])
        if checked.utcoffset()!=timedelta(hours=9):raise ValueError('JST required')
    except (ValueError,TypeError):
        raise ValueError('review_timestamp_not_JST')
    if not low <= final <= high or low > 0 or high < 0 or review['estimated_error'] <= 0:
        raise ValueError('review_inconsistent_range_or_error')
    if review['sign_judgment'] not in ['positive','negative','uncertain_near_zero']:
        raise ValueError('review_invalid_sign_judgment')
    if (review['sign_judgment']=='positive' and final<=0) or (review['sign_judgment']=='negative' and final>=0):
        raise ValueError('review_sign_conflict')
    return {**features, **{key:review[key] for key in required}, 'max_range':high-low,
            'reading_status':'work_visual_confirmed', 'difference_source':'image_estimate',
            'review_graph_sha256':review['graph_sha256']}
