"""Optional benchmark using actual consented, manually transcribed samples.
CSV columns: language,reference,transcript. No synthetic measurements."""
import csv, sys, json, unicodedata
from collections import defaultdict

def words(x): return unicodedata.normalize('NFC',x).casefold().split()
def distance(a,b):
    row=list(range(len(b)+1))
    for i,x in enumerate(a,1):
        new=[i]
        for j,y in enumerate(b,1): new.append(min(new[-1]+1,row[j]+1,row[j-1]+(x!=y)))
        row=new
    return row[-1]
def run(path):
    counts=defaultdict(lambda:[0,0,0])
    with open(path,encoding='utf-8-sig',newline='') as f:
        for r in csv.DictReader(f):
            ref=words(r['reference']); hyp=words(r['transcript']); c=counts[r['language']]; c[0]+=distance(ref,hyp); c[1]+=len(ref); c[2]+=1
    return {lang:{'samples':c[2],'word_error_rate_percent':round(100*c[0]/c[1],2) if c[1] else None} for lang,c in counts.items()}
if __name__=='__main__': print(json.dumps(run(sys.argv[1]),indent=2))
