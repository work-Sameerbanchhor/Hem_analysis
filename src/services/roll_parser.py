import re

PG_COURSE_CODES = {
    "037", "038", "041", "042", "043", "045", "046", "047", "049", "050",
    "053", "054", "057", "058", "059", "060", "061", "065", "066", "069",
    "070", "073", "076", "077", "078", "079", "080", "081", "082", "083",
    "084", "085", "089", "093", "097", "101", "109", "113", "117", "118",
    "119", "121", "125", "129", "135", "146", "151", "158", "159", "177",
    "179", "912",
    "55", "56", "57", "58", "59", "60", "61", "62", "63", "64", "67",
    "68", "69", "70", "71", "74", "75", "76", "77", "79", "80", "81",
    "82", "83", "85"
}

def sanitize_durg_roll_number(roll_str: str, course_name: str = ""):
    """
    Production Roll Number Sanitizer for Hemchand Yadav Vishwavidyalaya (RollNum.md Section 13).
    """
    if not roll_str:
        return "", False
    
    original = str(roll_str).strip()
    # Step 1: Strip ASCII noise (dots, slashes, dashes, spaces)
    clean_roll = re.sub(r'[^0-9]', '', original)
    modified = (clean_roll != original)
    
    c_name = course_name.lower()
    
    # Step 2: Fix Course-Code Misalignments under NEP 2020
    if "b.c.a" in c_name or "bca" in c_name:
        if len(clean_roll) == 10 and clean_roll.startswith('25') and clean_roll[5:7] == '10':
            clean_roll = clean_roll[:5] + '40' + clean_roll[7:]
            modified = True
            
    if "b.b.a" in c_name or "bba" in c_name:
        if len(clean_roll) == 10 and clean_roll.startswith('25') and clean_roll[5:7] == '50':
            clean_roll = clean_roll[:5] + '45' + clean_roll[7:]
            modified = True

    # Step 3: Handle Length Anomalies
    length = len(clean_roll)
    
    if length == 13 and clean_roll.startswith('231110001'):
        clean_roll = '23111001' + clean_roll[9:]
        modified = True
    elif length == 12 and clean_roll.startswith('75040104'):
        clean_roll = '750401' + clean_roll[8:]
        modified = True
    elif length == 12 and clean_roll.startswith('330200400'):
        clean_roll = '33020040' + clean_roll[9:]
        modified = True
    elif length == 11:
        if clean_roll.startswith('25') and '010' in clean_roll[5:8]:
            clean_roll = clean_roll[:5] + '10' + clean_roll[8:]
            modified = True
        elif clean_roll.startswith('25') and clean_roll[5:7] == '30' and clean_roll[7] == '0':
            clean_roll = clean_roll[:7] + clean_roll[8:]
            modified = True
        elif clean_roll.startswith('255331'):
            clean_roll = '25' + clean_roll[3:]
            modified = True
        elif clean_roll.startswith(('74', '76')):
            if clean_roll[4:6] == '11':
                clean_roll = clean_roll[:4] + clean_roll[5:]
                modified = True
            elif clean_roll[5:7] == '11':
                clean_roll = clean_roll[:5] + clean_roll[6:]
                modified = True
            elif clean_roll[6:8] == '00':
                clean_roll = clean_roll[:6] + clean_roll[7:]
                modified = True
    elif length == 9:
        if clean_roll.startswith('745100'):
            clean_roll = '745107' + clean_roll[5:]
            modified = True
        elif clean_roll.startswith('433840'):
            clean_roll = '433804' + clean_roll[5:]
            modified = True
        elif clean_roll.startswith('411150'):
            clean_roll = '411105' + clean_roll[5:]
            modified = True
    elif length == 7 and clean_roll.startswith('4331'):
        clean_roll = clean_roll + '0'
        modified = True

    return clean_roll, modified

def parse_roll(rollno: str):
    rollno = rollno.strip()
    length = len(rollno)
    
    year = None
    college = None
    course_code = None
    serial = None
    
    if rollno.startswith('9'):
        year = 2019
        college = rollno[1:4]
        course_code = rollno[4:7]
        serial = rollno[7:]
    elif length == 8:
        y_digit = int(rollno[0])
        year = 2020 + y_digit
        college = rollno[1:4]
        serial = rollno[4:]
    elif length == 10:
        year = 2000 + int(rollno[0:2])
        college = rollno[2:5]
        course_code = rollno[5:7]
        serial = rollno[7:]
    elif length == 11:
        if rollno[0:2] in ['19', '20', '21']:
            year = 2000 + int(rollno[0:2])
            college = rollno[2:5]
            course_code = rollno[5:8]
            serial = rollno[8:]
        elif len(rollno) >= 5 and rollno[3:5] == '18':
            year = 2018
            college = rollno[0:3]
            course_code = rollno[5:8]
            serial = rollno[8:]
        else:
            y_digit = int(rollno[0])
            if y_digit == 2:
                year = 2021
            elif y_digit == 3:
                year = 2023
            elif y_digit in [4, 5, 6]:
                year = 2020 + y_digit
            else:
                year = 2020
            college = rollno[1:4]
            course_code = rollno[4:7]
            serial = rollno[7:]
    elif length == 12:
        year = 2000 + int(rollno[0:2])
        college = rollno[2:5]
        course_code = rollno[5:8]
        serial = rollno[8:]
        
    return {
        "year": year,
        "college": college,
        "course_code": course_code,
        "serial": serial,
        "length": length
    }

def classify_roll(rollno: str, roll_info: dict):
    length = roll_info["length"]
    course_code = roll_info["course_code"]
    
    if length == 10:
        if course_code in PG_COURSE_CODES:
            return "pg"
        return "nep"
        
    is_bed_code = course_code in ["029", "46", "023", "29", "23", "033", "47", "047", "046"]
    
    is_pg_code = False
    if course_code:
        if course_code in PG_COURSE_CODES and not is_bed_code:
            is_pg_code = True
        elif course_code.isdigit() and int(course_code) >= 50 and not is_bed_code:
            is_pg_code = True
        
    if is_pg_code:
        return "pg"
        
    if length == 11:
        if rollno[0:2] in ['19', '20', '21'] and not is_bed_code:
            return "pg"
        if len(rollno) >= 5 and rollno[3:5] == '18' and not is_bed_code:
            return "pg"
            
    if length == 12:
        if is_pg_code:
            return "pg"
            
    if length == 8:
        return "annual"
            
    return "ug"
