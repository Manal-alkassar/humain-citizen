"""Evaluation datasets.

DEV        - utterances used while building the exemplars/lexicon (seen).
HELDOUT_A  - written before the first benchmark run; failures on this set were
             analysed once (iteration 1 -> 2), so it is no longer fully unseen.
HELDOUT_B  - written AFTER iteration 2 was frozen and never used for tuning.
             This is the honest generalisation number.
"""

DEV = [
    ("my car was stolen", "report_stolen_vehicle"),
    ("My car was stolen. I don't know what I need to do.", "report_stolen_vehicle"),
    ("someone stole my car from the parking", "report_stolen_vehicle"),
    ("سيارتي انسرقت", "report_stolen_vehicle"),
    ("I lost my driving licence. What do I do?", "replace_lost_licence"),
    ("I lost my licence", "replace_lost_licence"),
    ("ضاعت رخصتي", "replace_lost_licence"),
    ("I want to close my business", "close_business"),
    ("شطب السجل التجاري", "close_business"),
    ("I need to hire someone", "hire_employee"),
    ("I need to end an employee's contract", "end_employee_contract"),
    ("I want to leave my job", "leave_job"),
    ("what is the status of my case", "case_status"),
    ("hello what's the weather", None),
]

HELDOUT_A = [
    # stolen vehicle
    ("Someone drove off with my Hyundai overnight", "report_stolen_vehicle"),
    ("my car is gone from the driveway, I think it was stolen", "report_stolen_vehicle"),
    ("i think my car got stolen from the mall parking", "report_stolen_vehicle"),
    ("car stolen pls help", "report_stolen_vehicle"),
    ("My SUV was taken from outside the gym", "report_stolen_vehicle"),
    ("تم سرقة سيارتي من قدام البيت", "report_stolen_vehicle"),
    ("سرقوا سيارتي", "report_stolen_vehicle"),
    ("my vehical was stollen", "report_stolen_vehicle"),
    # licence
    ("I can't find my driving license anywhere", "replace_lost_licence"),
    ("misplaced my licence", "replace_lost_licence"),
    ("how do I get a replacement license", "replace_lost_licence"),
    ("My licence fell out of my wallet and is gone", "replace_lost_licence"),
    ("lost driving permit", "replace_lost_licence"),
    ("رخصتي ضايعة", "replace_lost_licence"),
    ("my license got stolen with my wallet", "replace_lost_licence"),
    ("I need a new licence, mine is torn", "replace_lost_licence"),
    # close business
    ("I want to wind down my company", "close_business"),
    ("how do I shut my cafe permanently", "close_business"),
    ("cancel the CR for my shop", "close_business"),
    ("ابغى اشطب السجل التجاري", "close_business"),
    ("closing my restaurant for good", "close_business"),
    # hire
    ("I want to recruit a cashier", "hire_employee"),
    ("I need a new driver for my company", "hire_employee"),
    ("how do I bring on a new staff member", "hire_employee"),
    ("ابي اوظف محاسب", "hire_employee"),
    # terminate
    ("I want to let go of one of my workers", "end_employee_contract"),
    ("how can I terminate an employee contract", "end_employee_contract"),
    ("fire one of my staff", "end_employee_contract"),
    ("ابغى انهي عقد عامل عندي", "end_employee_contract"),
    # resign
    ("I'm quitting", "leave_job"),
    ("I want to hand in my notice", "leave_job"),
    ("how do I resign from my company", "leave_job"),
    ("ابغى اقدم استقالتي", "leave_job"),
    # status
    ("any news on my report?", "case_status"),
    ("where is my licence request at?", "case_status"),
    ("وش صار على طلبي", "case_status"),
    # out of scope -> must NOT trigger a journey
    ("what's the weather in Riyadh", None),
    ("book me a flight to Jeddah", None),
    ("tell me a joke", None),
    ("how do I renew my passport", None),
    ("I want to pay my electricity bill", None),
    ("ابغى احجز موعد مستشفى", None),
]

# Written after iteration 2 was frozen. Do not tune on these.
HELDOUT_B = [
    ("Thieves took my Camry from the street last night", "report_stolen_vehicle"),
    ("I parked my car at the souq and now it's not there, I think someone stole it", "report_stolen_vehicle"),
    ("need to report my stolen pickup truck", "report_stolen_vehicle"),
    ("سيارتي مسروقة من الموقف", "report_stolen_vehicle"),
    ("help!! car got stolen!!", "report_stolen_vehicle"),
    ("vehicle theft - toyota", "report_stolen_vehicle"),
    ("I have lost my driving licence on the bus", "replace_lost_licence"),
    ("where do i get a replacement for a lost driving license", "replace_lost_licence"),
    ("my license is damaged and unreadable, need a new one", "replace_lost_licence"),
    ("فقدت رخصتي امس", "replace_lost_licence"),
    ("lisence lost", "replace_lost_licence"),
    ("I'm shutting down my small business", "close_business"),
    ("please cancel my commercial registration", "close_business"),
    ("we are dissolving the company", "close_business"),
    ("ابي اسكر المحل نهائيا", "close_business"),
    ("we're hiring a receptionist", "hire_employee"),
    ("I want to employ a new accountant", "hire_employee"),
    ("ابغى توظيف سائق", "hire_employee"),
    ("I have to dismiss an employee", "end_employee_contract"),
    ("how to lay off a worker", "end_employee_contract"),
    ("ابغى افصل عامل", "end_employee_contract"),
    ("I got a new job and want to resign from my current one", "leave_job"),
    ("thinking of quitting my job, what do I do", "leave_job"),
    ("ابغى استقيل من شغلي", "leave_job"),
    ("track my case please", "case_status"),
    ("has anything happened with my request?", "case_status"),
    ("متابعة البلاغ", "case_status"),
    ("what time does the mall close", None),
    ("recommend a good restaurant", None),
    ("how much is a visa to the UK", None),
    ("my phone screen is broken", None),
    ("وش احسن مطعم في الرياض", None),
]
