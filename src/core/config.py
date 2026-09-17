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

# College Center Codes (84 verified university centers across all districts)
COLLEGES = [
    '101', '102', '103', '104', '105', '106', '107', '108', '109', '110', '113', '122', '123',
    '201', '202', '203', '204', '205', '206', '207', '211', '212', '213', '221',
    '302', '303', '304', '305', '306', '307', '308', '309', '310', '311', '312', '313', '314',
    '316', '317', '318', '319', '320', '331', '332', '333', '334', '335', '337', '338', '339',
    '340', '341', '344', '370', '381', '384',
    '401', '402', '403', '404', '405', '406', '407', '413',
    '502', '503', '504', '505', '506', '507', '508', '509', '510', '511', '514', '515', '516',
    '517', '519', '522', '524', '525', '532'
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

# Complete Course Mapping (Empirically verified across 47,500+ records)
COURSE_MAP = {
    # Education Faculty (B.Ed & B.P.Ed)
    "029": {"keywords": ["bachelor of education", "b.ed"], "exclusions": ["b.sc", "b.a", "m.ed", "physical"]},
    "46":  {"keywords": ["bachelor of education", "b.ed"], "exclusions": ["b.sc", "b.a", "m.ed", "physical"]},
    "033": {"keywords": ["physical education", "b.p.ed", "bped"], "exclusions": []},
    "47":  {"keywords": ["physical education", "b.p.ed", "bped"], "exclusions": []},
    
    # 4-Year Integrated Education (B.Sc.-B.Ed & B.A.-B.Ed)
    "137": {"keywords": ["b.sc.-b.ed", "b.sc. b.ed"], "exclusions": []},
    "138": {"keywords": ["b.sc.-b.ed", "b.sc. b.ed"], "exclusions": []},
    "139": {"keywords": ["b.sc.-b.ed", "b.sc. b.ed"], "exclusions": []},
    "140": {"keywords": ["b.sc.-b.ed", "b.sc. b.ed"], "exclusions": []},
    "141": {"keywords": ["b.a.-b.ed", "b.a. b.ed"], "exclusions": []},
    "142": {"keywords": ["b.a.-b.ed", "b.a. b.ed"], "exclusions": []},
    "143": {"keywords": ["b.a.-b.ed", "b.a. b.ed"], "exclusions": []},
    "144": {"keywords": ["b.a.-b.ed", "b.a. b.ed"], "exclusions": []},
    
    # BBA (Bachelor of Business Administration)
    "017": {"keywords": ["b.b.a", "bba", "business administration", "bachelor of business administration"], "exclusions": ["mba"]},
    "45":  {"keywords": ["b.b.a", "bba", "business administration", "bachelor of business administration"], "exclusions": ["mba"]},
    "50":  {"keywords": ["b.b.a", "bba", "business administration", "bachelor of business administration"], "exclusions": ["mba"]},
    
    # Law Faculty (LL.B. & LL.M.)
    "023": {"keywords": ["ll.b", "llb", "bachelor of laws", "laws"], "exclusions": ["ll.m", "llm"]},
    "48":  {"keywords": ["ll.b", "llb", "bachelor of laws", "laws"], "exclusions": ["ll.m", "llm"]},
    "70":  {"keywords": ["ll.m", "llm", "master of laws"], "exclusions": []},
    
    # BCA (Bachelor of Computer Applications)
    "007": {"keywords": ["computer application", "bca", "b.c.a"], "exclusions": []},
    "011": {"keywords": ["computer application", "bca", "b.c.a"], "exclusions": []},
    "013": {"keywords": ["computer application", "bca", "b.c.a"], "exclusions": []},
    "014": {"keywords": ["computer application", "bca", "b.c.a"], "exclusions": []},
    "015": {"keywords": ["computer application", "bca", "b.c.a"], "exclusions": []},
    "40":  {"keywords": ["computer application", "bca", "b.c.a"], "exclusions": []},
    
    # B.Sc. (Bachelor of Science)
    "008": {"keywords": ["b.sc", "bachelor of science"], "exclusions": ["b.sc.-b.ed", "b.sc. b.ed", "m.sc", "home science", "b.h.sc."]},
    "009": {"keywords": ["b.sc", "bachelor of science"], "exclusions": ["b.sc.-b.ed", "b.sc. b.ed", "m.sc", "home science", "b.h.sc."]},
    "010": {"keywords": ["b.sc", "bachelor of science"], "exclusions": ["b.sc.-b.ed", "b.sc. b.ed", "m.sc", "home science", "b.h.sc."]},
    "012": {"keywords": ["b.sc", "bachelor of science"], "exclusions": ["b.sc.-b.ed", "b.sc. b.ed", "m.sc", "home science", "b.h.sc."]},
    "090": {"keywords": ["b.sc", "bachelor of science"], "exclusions": ["b.sc.-b.ed", "b.sc. b.ed", "m.sc", "home science", "b.h.sc."]},
    "30":  {"keywords": ["b.sc", "bachelor of science"], "exclusions": ["b.sc.-b.ed", "b.sc. b.ed", "m.sc", "home science", "b.h.sc."]},
    
    # B.Com. (Bachelor of Commerce)
    "004": {"keywords": ["b.com", "bachelor of commerce"], "exclusions": ["m.com"]},
    "005": {"keywords": ["b.com", "bachelor of commerce"], "exclusions": ["m.com"]},
    "006": {"keywords": ["b.com", "bachelor of commerce"], "exclusions": ["m.com"]},
    "20":  {"keywords": ["b.com", "bachelor of commerce"], "exclusions": ["m.com"]},
    
    # B.A. (Bachelor of Arts)
    "001": {"keywords": ["b.a.", "bachelor of arts", " b.a "], "exclusions": ["b.a.-b.ed", "b.a. b.ed", "m.a.", "b.a. (ll.b.)"]},
    "002": {"keywords": ["b.a.", "bachelor of arts", " b.a "], "exclusions": ["b.a.-b.ed", "b.a. b.ed", "m.a.", "b.a. (ll.b.)"]},
    "003": {"keywords": ["b.a.", "bachelor of arts", " b.a "], "exclusions": ["b.a.-b.ed", "b.a. b.ed", "m.a.", "b.a. (ll.b.)"]},
    "030": {"keywords": ["b.a.", "bachelor of arts", " b.a "], "exclusions": ["b.a.-b.ed", "b.a. b.ed", "m.a.", "b.a. (ll.b.)"]},
    "10":  {"keywords": ["b.a.", "bachelor of arts", " b.a "], "exclusions": ["b.a.-b.ed", "b.a. b.ed", "m.a.", "b.a. (ll.b.)"]},
    
    # Library Science (B.Lib & M.Lib)
    "016": {"keywords": ["b.lib", "bachelor of library", "library"], "exclusions": ["m.lib"]},
    "121": {"keywords": ["m.lib", "master of library", "library", "ms lib i sc"], "exclusions": ["b.lib"]},
    "69":  {"keywords": ["library", "m.lib", "m.lib.", "ms lib i sc", "bachelor of library", "b.lib"], "exclusions": []},

    # M.Sc. Legacy (3-Digit) & NEP (2-Digit)
    "073": {"keywords": ["botany", "m sc botany", "m.sc. botany"], "exclusions": []},
    "62":  {"keywords": ["botany", "m sc botany", "m.sc. botany"], "exclusions": []},
    "077": {"keywords": ["zoology", "m sc zoology", "m.sc. zoology"], "exclusions": []},
    "63":  {"keywords": ["zoology", "m sc zoology", "m.sc. zoology"], "exclusions": []},
    "078": {"keywords": ["physics", "m sc physics", "m.sc. physics"], "exclusions": []},
    "085": {"keywords": ["physics", "m sc physics", "m.sc. physics"], "exclusions": []},
    "67":  {"keywords": ["physics", "m sc physics", "m.sc. physics"], "exclusions": []},
    "079": {"keywords": ["chemistry", "m.sc.ch", "m.sc. chemistry", "m sc chemistry"], "exclusions": []},
    "093": {"keywords": ["chemistry", "m.sc.ch", "m.sc. chemistry", "m sc chemistry"], "exclusions": []},
    "77":  {"keywords": ["chemistry", "m.sc.ch", "m.sc. chemistry", "m sc chemistry"], "exclusions": []},
    "080": {"keywords": ["botany", "m sc botany", "m.sc. botany"], "exclusions": []},
    "081": {"keywords": ["mathematics", "m sc mathematics", "m.sc. mathematics"], "exclusions": []},
    "082": {"keywords": ["mathematics", "m sc mathematics", "m.sc. mathematics"], "exclusions": []},
    "083": {"keywords": ["mathematics", "m sc mathematics", "m.sc. mathematics"], "exclusions": []},
    "64":  {"keywords": ["mathematics", "m sc mathematics", "m.sc. mathematics"], "exclusions": []},
    "084": {"keywords": ["bio technology", "m sc bio tech", "m.sc. bio tech", "biotechnology"], "exclusions": []},
    "089": {"keywords": ["bio technology", "m sc bio tech", "m.sc. bio tech", "biotechnology"], "exclusions": []},
    "68":  {"keywords": ["bio technology", "m sc bio tech", "m.sc. bio tech", "biotechnology"], "exclusions": []},
    "097": {"keywords": ["computer science", "m.sc.c.s.", "m.sc. computer science", "m sc computer science"], "exclusions": []},
    "81":  {"keywords": ["computer science", "m.sc.c.s.", "m.sc. computer science", "m sc computer science"], "exclusions": []},
    "101": {"keywords": ["microbiology", "m.sc.microbio", "m.sc. microbiology", "m sc microbiology"], "exclusions": []},
    "82":  {"keywords": ["microbiology", "m.sc.microbio", "m.sc. microbiology", "m sc microbiology"], "exclusions": []},
    "109": {"keywords": ["human development", "home science"], "exclusions": []},
    "113": {"keywords": ["food", "nutrition", "home science"], "exclusions": []},
    "80":  {"keywords": ["home science"], "exclusions": []},
    "83":  {"keywords": ["home science", "textile"], "exclusions": []},
    "85":  {"keywords": ["home science", "food", "nutrition"], "exclusions": []},

    # M.A. Legacy (3-Digit) & NEP (2-Digit)
    "037": {"keywords": ["hindi", "m.a.h", "m.a. hindi", "m.a hindi"], "exclusions": []},
    "038": {"keywords": ["hindi", "m.a.h", "m.a. hindi", "m.a hindi"], "exclusions": []},
    "75":  {"keywords": ["hindi", "m.a.h", "m.a. hindi", "m.a hindi"], "exclusions": []},
    "041": {"keywords": ["english", "m.a english", "m.a. english"], "exclusions": []},
    "042": {"keywords": ["english", "m.a english", "m.a. english"], "exclusions": []},
    "043": {"keywords": ["english", "m.a english", "m.a. english"], "exclusions": []},
    "55":  {"keywords": ["english", "m.a english", "m.a. english"], "exclusions": []},
    "045": {"keywords": ["sociology", "m.a sociology", "m.a. sociology"], "exclusions": []},
    "046": {"keywords": ["sociology", "m.a sociology", "m.a. sociology"], "exclusions": []},
    "047": {"keywords": ["sociology", "m.a sociology", "m.a. sociology"], "exclusions": []},
    "56":  {"keywords": ["sociology", "m.a sociology", "m.a. sociology"], "exclusions": []},
    "049": {"keywords": ["economics", "m.a economics", "m.a. economics"], "exclusions": []},
    "050": {"keywords": ["economics", "m.a economics", "m.a. economics"], "exclusions": []},
    "57":  {"keywords": ["economics", "m.a economics", "m.a. economics"], "exclusions": []},
    "053": {"keywords": ["geography", "m.a geography", "m.a. geography"], "exclusions": []},
    "054": {"keywords": ["geography", "m.a geography", "m.a. geography"], "exclusions": []},
    "58":  {"keywords": ["geography", "m.a geography", "m.a. geography"], "exclusions": []},
    "057": {"keywords": ["political science", "m.a political sc", "m.a. political sc"], "exclusions": []},
    "058": {"keywords": ["political science", "m.a political sc", "m.a. political sc"], "exclusions": []},
    "059": {"keywords": ["political science", "m.a political sc", "m.a. political sc"], "exclusions": []},
    "59":  {"keywords": ["political science", "m.a political sc", "m.a. political sc"], "exclusions": []},
    "065": {"keywords": ["history", "m.a history", "m.a. history"], "exclusions": []},
    "066": {"keywords": ["history", "m.a history", "m.a. history"], "exclusions": []},
    "60":  {"keywords": ["history", "m.a history", "m.a. history"], "exclusions": []},
    "070": {"keywords": ["psychology", "m.a psychology", "m.a. psychology"], "exclusions": []},
    "61":  {"keywords": ["psychology", "m.a psychology", "m.a. psychology"], "exclusions": []},
    "146": {"keywords": ["sanskrit", "m.a sanskrit", "m.a. sanskrit"], "exclusions": []},
    "151": {"keywords": ["sanskrit", "m.a sanskrit", "m.a. sanskrit"], "exclusions": []},
    "158": {"keywords": ["philosophy", "m.a philosophy", "m.a. philosophy"], "exclusions": []},
    "159": {"keywords": ["philosophy", "m.a philosophy", "m.a. philosophy"], "exclusions": []},
    
    # M.Com. (Master of Commerce)
    "076": {"keywords": ["m.com", "master of commerce"], "exclusions": []},
    "117": {"keywords": ["m.com", "master of commerce"], "exclusions": []},
    "118": {"keywords": ["m.com", "master of commerce"], "exclusions": []},
    "119": {"keywords": ["m.com", "master of commerce"], "exclusions": []},
    "76":  {"keywords": ["m.com", "master of commerce"], "exclusions": []},

    # M.Ed. (Master of Education)
    "129": {"keywords": ["m.ed", "master of education"], "exclusions": ["b.ed"]},
    "912": {"keywords": ["m.ed", "master of education"], "exclusions": ["b.ed"]},
    "79":  {"keywords": ["m.ed", "master of education"], "exclusions": ["b.ed"]},

    # Social Work & Mental Health
    "125": {"keywords": ["m.s.w", "msw", "social work"], "exclusions": []},
    "74":  {"keywords": ["m.s.w", "msw", "social work"], "exclusions": []},
    "177": {"keywords": ["psychiatric", "social work", "m.phil"], "exclusions": []},

    # Diplomas & Post Graduate Diplomas
    "133": {"keywords": ["d.c.a", "dca", "diploma in computer application"], "exclusions": ["pgdca", "p.g.d.c.a"]},
    "613": {"keywords": ["d.c.a", "dca", "diploma in computer application"], "exclusions": ["pgdca", "p.g.d.c.a"]},
    "71":  {"keywords": ["pgdca", "p.g.d.c.a"], "exclusions": []},
    "135": {"keywords": ["pgdca", "p.g.d.c.a"], "exclusions": []},
    "179": {"keywords": ["pgdgc", "guidance", "counselling"], "exclusions": []}
}
