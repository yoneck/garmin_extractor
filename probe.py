import logging
logging.disable(logging.WARNING)
from pathlib import Path
from gar.utils.config import load_from_env
from gar.extraction.login import connect

if __name__ == "__main__":
    project_root = Path(__file__).resolve().parent
    cfg = load_from_env(project_root / ".env", strict=True)
    g = connect(cfg)
    print("CONNECT_OK")
    acts = g.get_activities(0, 1)
    aid = acts[0]["activityId"]
    print("ID", aid)
    b = g.download_activity(aid)
    print("DL-tcx", len(b))
    from garminconnect import Garmin as _G
    rf = g.download_activity(aid, _G.ActivityDownloadFormat.ORIGINAL)
    import zipfile, io, os
    z = zipfile.ZipFile(io.BytesIO(rf))
    fname = list(z.namelist())[0]
    fitz = z.read(fname)
    open("/tmp/raw.fit","wb").write(fitz)
    print("DL-fit", len(fitz))
    from fitparse import FitFile
    ff = FitFile(fitz)
    fields=set(); count=0
    for fd in ff:
        count+=1
        fields.update(fd.record.fields.keys())
    from collections import defaultdict
    per=defaultdict(list); n=0
    for fd in ff:
        if fd.name and fd.name not in ("field_definition_message","command_message"):
            for f in fd.field_values: per[fd.name].append({f.name:f.value})
            n+=1
            if n>2: break
    print("MESSAGES",count)
    import json
    print("FIELDS",sorted(fields))
    print(json.dumps({k:v[:1] for k,v in per.items()},default=str)[:1200])
