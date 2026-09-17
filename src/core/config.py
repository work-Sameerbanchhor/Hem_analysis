import os

# Root directory of the project
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Amazon AWS RDS PostgreSQL Configuration
RDS_HOST = os.environ.get("RDS_HOST", "hemchand-university-database.cdqk2yemwwg9.ap-south-1.rds.amazonaws.com")
RDS_PORT = int(os.environ.get("RDS_PORT", 5432))
RDS_DATABASE = os.environ.get("RDS_DATABASE", "postgres")
RDS_USER = os.environ.get("RDS_USER", "Sameer")
RDS_PASSWORD = os.environ.get("RDS_PASSWORD", "Sameer2002")
CERT_PATH = os.environ.get("RDS_CERT_PATH", os.path.join(BASE_DIR, "global-bundle.pem"))

# Portal URLs
LEGACY_URL = "https://durg.ucanapply.com/result-details"
NEP_URL = "https://durgnep.ucanapply.com/result-details"

# Logging
LOG_PATH = os.path.join(BASE_DIR, "searches.log")

# College Center Codes (21 centers)
COLLEGES = [
    '303', '304', '305', '307', '331', '332', '333', '334', '335',
    '337', '338', '339', '340', '341', '344', '352', '358', '366', '374', '381', '386'
]

# Shared Global State
UNIFIED_CONFIGS = []
LAST_INDEX_CHECK_TIME = 0
CRAWL_STATUS = {
    "status": "idle",
    "current_exam": None,
    "colleges_progress": "",
    "records_added": 0,
    "started_at": None,
    "elapsed_seconds": 0
}

# Complete Course Mapping (RollNum.md Section 13 & Legacy Patterns)
COURSE_MAP = {
    # B.Ed and B.P.Ed
    "029": {"keywords": ["bachelor of education", "b.ed"], "exclusions": ["b.sc", "b.a", "m.ed", "physical"]},
    "46": {"keywords": ["bachelor of education", "b.ed"], "exclusions": ["b.sc", "b.a", "m.ed", "physical"]},
    "023": {"keywords": ["bachelor of education", "b.ed", "new syllabus"], "exclusions": []},
    
    "033": {"keywords": ["physical education", "b.p.ed", "bped"], "exclusions": []},
    "47": {"keywords": ["physical education", "b.p.ed", "bped"], "exclusions": []},
    
    # Integrated Ed
    "137": {"keywords": ["b.sc.-b.ed", "b.sc. b.ed"], "exclusions": []},
    "140": {"keywords": ["b.sc.-b.ed", "b.sc. b.ed"], "exclusions": []},
    "144": {"keywords": ["b.a.-b.ed", "b.a. b.ed"], "exclusions": []},
    
    # BBA
    "45": {"keywords": ["b.b.a", "bba", "business administration", "bachelor of business administration"], "exclusions": ["mba"]},
    
    # Law (LL.B.)
    "48": {"keywords": ["ll.b", "llb", "bachelor of laws", "laws"], "exclusions": ["ll.m", "llm"]},
    
    # BCA
    "007": {"keywords": ["computer application", "bca", "b.c.a"], "exclusions": []},
    "011": {"keywords": ["computer application", "bca", "b.c.a"], "exclusions": []},
    "013": {"keywords": ["computer application", "bca", "b.c.a"], "exclusions": []},
    "014": {"keywords": ["computer application", "bca", "b.c.a"], "exclusions": []},
    "015": {"keywords": ["computer application", "bca", "b.c.a"], "exclusions": []},
    "40": {"keywords": ["computer application", "bca", "b.c.a"], "exclusions": []},
    
    # B.Sc.
    "008": {"keywords": ["b.sc", "bachelor of science"], "exclusions": ["b.sc.-b.ed", "b.sc. b.ed", "m.sc", "home science", "b.h.sc."]},
    "009": {"keywords": ["b.sc", "bachelor of science"], "exclusions": ["b.sc.-b.ed", "b.sc. b.ed", "m.sc", "home science", "b.h.sc."]},
    "010": {"keywords": ["b.sc", "bachelor of science"], "exclusions": ["b.sc.-b.ed", "b.sc. b.ed", "m.sc", "home science", "b.h.sc."]},
    "012": {"keywords": ["b.sc", "bachelor of science"], "exclusions": ["b.sc.-b.ed", "b.sc. b.ed", "m.sc", "home science", "b.h.sc."]},
    "007": {"keywords": ["b.sc", "bachelor of science"], "exclusions": ["b.sc.-b.ed", "b.sc. b.ed", "m.sc", "home science", "b.h.sc."]},
    "30": {"keywords": ["b.sc", "bachelor of science"], "exclusions": ["b.sc.-b.ed", "b.sc. b.ed", "m.sc", "home science", "b.h.sc."]},
    
    # B.Com.
    "004": {"keywords": ["b.com", "bachelor of commerce"], "exclusions": ["m.com"]},
    "005": {"keywords": ["b.com", "bachelor of commerce"], "exclusions": ["m.com"]},
    "006": {"keywords": ["b.com", "bachelor of commerce"], "exclusions": ["m.com"]},
    "20": {"keywords": ["b.com", "bachelor of commerce"], "exclusions": ["m.com"]},
    
    # B.A.
    "001": {"keywords": ["b.a.", "bachelor of arts", " b.a "], "exclusions": ["b.a.-b.ed", "b.a. b.ed", "m.a.", "b.a. (ll.b.)"]},
    "002": {"keywords": ["b.a.", "bachelor of arts", " b.a "], "exclusions": ["b.a.-b.ed", "b.a. b.ed", "m.a.", "b.a. (ll.b.)"]},
    "003": {"keywords": ["b.a.", "bachelor of arts", " b.a "], "exclusions": ["b.a.-b.ed", "b.a. b.ed", "m.a.", "b.a. (ll.b.)"]},
    "10": {"keywords": ["b.a.", "bachelor of arts", " b.a "], "exclusions": ["b.a.-b.ed", "b.a. b.ed", "m.a.", "b.a. (ll.b.)"]},
    
    # M.Sc.
    "077": {"keywords": ["mathematics", "m sc mathematics", "m.sc. mathematics"], "exclusions": []},
    "078": {"keywords": ["physics", "m sc physics", "m.sc. physics"], "exclusions": []},
    "079": {"keywords": ["chemistry", "m.sc.ch", "m.sc. chemistry", "m sc chemistry"], "exclusions": []},
    "080": {"keywords": ["botany", "m sc botany", "m.sc. botany"], "exclusions": []},
    "081": {"keywords": ["zoology", "m sc zoology", "m.sc. zoology"], "exclusions": []},
    "082": {"keywords": ["geology", "m sc geology", "m.sc. geology"], "exclusions": []},
    "083": {"keywords": ["microbiology", "m.sc.microbio", "m.sc. microbiology", "m sc microbiology"], "exclusions": []},
    "084": {"keywords": ["bio technology", "m sc bio tech", "m.sc. bio tech", "biotechnology"], "exclusions": []},
    "085": {"keywords": ["computer science", "m.sc.c.s.", "m.sc. computer science", "m sc computer science"], "exclusions": []},
    
    # M.A.
    "037": {"keywords": ["economics", "m.a economics", "m.a. economics"], "exclusions": []},
    "038": {"keywords": ["economics", "m.a economics", "m.a. economics"], "exclusions": []},
    "041": {"keywords": ["geography", "m.a geography", "m.a. geography"], "exclusions": []},
    "042": {"keywords": ["geography", "m.a geography", "m.a. geography"], "exclusions": []},
    "045": {"keywords": ["history", "m.a history", "m.a. history"], "exclusions": []},
    "046": {"keywords": ["history", "m.a history", "m.a. history"], "exclusions": []},
    "049": {"keywords": ["political science", "m.a political sc", "m.a. political sc"], "exclusions": []},
    "050": {"keywords": ["political science", "m.a political sc", "m.a. political sc"], "exclusions": []},
    "053": {"keywords": ["sociology", "m.a sociology", "m.a. sociology"], "exclusions": []},
    "054": {"keywords": ["sociology", "m.a sociology", "m.a. sociology"], "exclusions": []},
    "057": {"keywords": ["english", "m.a english", "m.a. english"], "exclusions": []},
    "058": {"keywords": ["english", "m.a english", "m.a. english"], "exclusions": []},
    "065": {"keywords": ["hindi", "m.a.h", "m.a. hindi", "m.a hindi"], "exclusions": []},
    "066": {"keywords": ["hindi", "m.a.h", "m.a. hindi", "m.a hindi"], "exclusions": []},
    "069": {"keywords": ["sanskrit", "m.a sanskrit", "m.a. sanskrit"], "exclusions": []},
    "073": {"keywords": ["psychology", "m.a psychology", "m.a. psychology"], "exclusions": []},
    
    # M.Com.
    "076": {"keywords": ["m.com", "master of commerce"], "exclusions": []},
    
    # Post Graduate Diplomas
    "71": {"keywords": ["pgdca", "p.g.d.c.a"], "exclusions": []},
    "135": {"keywords": ["pgdca", "p.g.d.c.a"], "exclusions": []},
    "179": {"keywords": ["pgdgc", "guidance", "counselling"], "exclusions": []}
}
