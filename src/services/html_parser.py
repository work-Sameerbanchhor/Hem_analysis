import re
import html
from bs4 import BeautifulSoup

def normalize_title(title: str):
    if not title:
        return ""
    title = title.lower()
    title = re.sub(r'[^a-z0-9]', '', title)
    return title

def is_regular_or_private_exam(title: str, link: str):
    title = title.lower()
    is_supply_or_ex = any(k in title for k in ["supply", "atkt", "ex ", " ex", "reval", "revaluation", "re-totaling"])
    return not is_supply_or_ex

def is_strictly_regular_or_private(desc: str, source: str) -> bool:
    desc_lower = desc.lower()
    if any(k in desc_lower for k in ["supply", "atkt", "ex ", " ex", "reval", "revaluation", "re-totaling"]):
        return False
    if source == "legacy":
        return "regular" in desc_lower or "private" in desc_lower
    elif source == "nep":
        return True
    return False

def is_relevant_exam(desc: str, source: str) -> bool:
    desc_lower = desc.lower()
    if "home science" in desc_lower or "b.h.sc." in desc_lower:
        return False
    if source == "nep":
        nep_keywords = [
            "b.a.", "b.com", "b.sc", "b.c.a", "b.b.a",
            "bachelor of arts", "bachelor of science", "bachelor of commerce",
            "computer application", "business administration"
        ]
        return any(kw in desc_lower for kw in nep_keywords)
    else:
        legacy_keywords = [
            "b.a.", "b.com", "b.sc", "bca",
            "bachelor of arts", "bachelor of science", "bachelor of commerce",
            "computer application", " b.a "
        ]
        return any(kw in desc_lower for kw in legacy_keywords)

def parse_hyu_html(html_content: str):
    soup = BeautifulSoup(html_content, 'html.parser')
    student_info = {}
    
    label_tags = soup.find_all(['strong', 'b'])
    for tag in label_tags:
        text = tag.get_text().strip().lower().replace(':', '').strip()
        if not text:
            continue
        parent_td = tag.find_parent('td')
        if not parent_td:
            continue
        parent_tr = parent_td.find_parent('tr')
        if not parent_tr:
            continue
        
        tds = parent_tr.find_all('td')
        try:
            idx = tds.index(parent_td)
            if idx + 2 < len(tds):
                val = tds[idx+2].get_text().strip()
                val = re.sub(r'\s+', ' ', val)
                
                if text in ['roll no.', 'roll no', 'roll number']:
                    student_info['roll_no'] = val
                elif text in ['enrollment no.', 'enrollment no', 'enrollment number', 'enrollment']:
                    student_info['enrollment_no'] = val
                elif text in ['name of the student', 'student name', 'name']:
                    student_info['name'] = val
                elif "father's name" in text or 'father name' in text:
                    student_info['father_name'] = val
                elif "mother's name" in text or 'mother name' in text:
                    student_info['mother_name'] = val
                elif text == 'college':
                    student_info['college'] = val
                elif text in ['center', 'exam center']:
                    student_info['center'] = val
                elif text in ['status', 'student type']:
                    student_info['student_type'] = val
            elif idx + 1 < len(tds):
                val = tds[idx+1].get_text().strip()
                val = re.sub(r'\s+', ' ', val)
                if text in ['roll no.', 'roll no', 'roll number']:
                    student_info['roll_no'] = val
        except ValueError:
            pass
            
    photo_url = ""
    img_tag = soup.find('img', alt=lambda a: a and 'photo' in a.lower())
    if not img_tag:
        img_tag = soup.find('img', src=lambda s: s and '/photo/' in s.lower())
    if img_tag:
        photo_url = img_tag.get('src', '')
    student_info['photo_url'] = photo_url
            
    exam_title = ""
    h2_tag = soup.find('h2')
    if h2_tag:
        exam_title = h2_tag.get_text().strip()
        exam_title = re.sub(r'\s+', ' ', exam_title)

    if not exam_title:
        title_td = soup.find('td', style=lambda s: s and ('font-size:16px' in s.lower() or 'font-weight:bold' in s.lower()))
        if not title_td:
            title_td = soup.find('td', align="center", valign="middle")
        if title_td:
            txt = title_td.get_text().strip()
            txt = re.sub(r'\s+', ' ', txt)
            if txt.lower() not in ["course code", "course code :", "result details", "roll number"] and not txt.lower().startswith("course code"):
                exam_title = txt
        
    result_status = "N/A"
    for tag in label_tags:
        text = tag.get_text().strip().lower()
        if 'result' in text and text not in ['result details', 'result declared on -', 'result details:']:
            parent_td = tag.find_parent('td')
            if parent_td:
                parent_tr = parent_td.find_parent('tr')
                if parent_tr:
                    tds = parent_tr.find_all('td')
                    try:
                        idx = tds.index(parent_td)
                        if idx + 1 < len(tds):
                            val = tds[idx+1].get_text().strip()
                            val = re.sub(r'\s+', ' ', val)
                            if val:
                                result_status = val
                    except ValueError:
                        pass
                        
    sgpa = "N/A"
    for tag in label_tags:
        text = tag.get_text().strip().lower()
        if 'sgpa' in text:
            parent_td = tag.find_parent('td')
            if parent_td:
                parent_tr = parent_td.find_parent('tr')
                if parent_tr:
                    tds = parent_tr.find_all('td')
                    try:
                        idx = tds.index(parent_td)
                        if idx + 1 < len(tds):
                            val = tds[idx+1].get_text().strip()
                            val = re.sub(r'\s+', ' ', val)
                            if val:
                                sgpa = val
                    except ValueError:
                        pass
                        
    return {
        "exam_title": exam_title,
        "student_info": student_info,
        "result_status": result_status,
        "sgpa": sgpa
    }
