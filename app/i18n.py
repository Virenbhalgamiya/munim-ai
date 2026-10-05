"""Interface languages: हिंदी (hi, default), English (en), मराठी (mr).

The language travels in a ContextVar (set per request from the UI's language switch), so the dashboard,
insights, briefing and tool messages all come out in the language the person at the screen chose.
Input in any language (Hinglish included) is always understood.
"""
from __future__ import annotations

from contextvars import ContextVar
from datetime import date

LANGS = ("hi", "en", "mr")
current_lang: ContextVar[str] = ContextVar("current_lang", default="hi")


def lang_or_default(lang: str | None) -> str:
    return lang if lang in LANGS else "hi"


WEEKDAYS_EN = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
WEEKDAY = {
    "hi": dict(zip(WEEKDAYS_EN, ["सोमवार", "मंगलवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार", "रविवार"])),
    "mr": dict(zip(WEEKDAYS_EN, ["सोमवार", "मंगळवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार", "रविवार"])),
    "en": dict(zip(WEEKDAYS_EN, WEEKDAYS_EN)),
}
WEEKDAY_SHORT = {
    "hi": dict(zip(WEEKDAYS_EN, ["सोम", "मंगल", "बुध", "गुरु", "शुक्र", "शनि", "रवि"])),
    "mr": dict(zip(WEEKDAYS_EN, ["सोम", "मंगळ", "बुध", "गुरु", "शुक्र", "शनि", "रवि"])),
    "en": dict(zip(WEEKDAYS_EN, ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])),
}
MONTHS = {
    "hi": ["जनवरी", "फ़रवरी", "मार्च", "अप्रैल", "मई", "जून", "जुलाई", "अगस्त", "सितंबर", "अक्टूबर", "नवंबर", "दिसंबर"],
    "mr": ["जानेवारी", "फेब्रुवारी", "मार्च", "एप्रिल", "मे", "जून", "जुलै", "ऑगस्ट", "सप्टेंबर", "ऑक्टोबर", "नोव्हेंबर", "डिसेंबर"],
    "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"],
}
CATEGORY = {
    "hi": {"groceries": "किराना", "snacks": "स्नैक्स", "dairy": "डेयरी", "household": "घरेलू सामान"},
    "mr": {"groceries": "किराणा", "snacks": "स्नॅक्स", "dairy": "डेअरी", "household": "घरगुती सामान"},
    "en": {"groceries": "Groceries", "snacks": "Snacks", "dairy": "Dairy", "household": "Household"},
}


def date_label(d: date, lang: str) -> str:
    return f"{d.day} {MONTHS[lang][d.month - 1]}"


def date_display(d: date, lang: str) -> str:
    return f"{WEEKDAY[lang][WEEKDAYS_EN[d.weekday()]]}, {date_label(d, lang)}"


def name_for(record: dict, lang: str, key: str = "customer_name") -> str:
    """English shows the stored (Latin) name; Hindi/Marathi show the Devanagari name when known."""
    latin = record.get(key) or record.get("name") or ""
    if lang == "en":
        return latin
    return record.get(f"{key}_hi") or record.get("name_hi") or latin


def hour_label(h: int, lang: str) -> str:
    n = (h - 1) % 12 + 1
    if lang == "en":
        return f"{n} {'am' if h < 12 else 'pm'}"
    part = {"hi": ("सुबह", "दोपहर", "शाम", "रात"), "mr": ("सकाळी", "दुपारी", "संध्याकाळी", "रात्री")}[lang]
    return f"{part[0 if h < 12 else 1 if h < 16 else 2 if h < 20 else 3]} {n}"


def join_names(names: list[str], lang: str) -> str:
    if len(names) == 1:
        return names[0]
    word = {"hi": "और", "mr": "आणि", "en": "and"}[lang]
    return f"{', '.join(names[:-1])} {word} {names[-1]}"


# --------------------------------------------------------------------- message catalog
T: dict[str, dict[str, str]] = {
    # KPI tiles
    "kpi.sales": {"hi": "आज की बिक्री", "en": "Today's sales", "mr": "आजची विक्री"},
    "kpi.sales.delta": {"hi": "पिछले 4 {wd} से", "en": "vs last 4 {wd}s", "mr": "मागील 4 {wd} पेक्षा"},
    "kpi.sales.trend": {"hi": "पिछले 14 दिन", "en": "Last 14 days", "mr": "मागील 14 दिवस"},
    "kpi.payments": {"hi": "UPI पेमेंट", "en": "UPI payments", "mr": "UPI पेमेंट"},
    "kpi.payments.sub": {"hi": "औसत बिल {v}", "en": "Average bill {v}", "mr": "सरासरी बिल {v}"},
    "kpi.udhaar": {"hi": "बाकी उधार", "en": "Udhaar outstanding", "mr": "बाकी उधारी"},
    "kpi.udhaar.sub": {"hi": "{n} ग्राहक · {o} देर से", "en": "{n} customers · {o} overdue", "mr": "{n} ग्राहक · {o} थकीत"},
    "kpi.risk": {"hi": "हफ़्ते का धंधा खतरे में", "en": "Weekly business at risk", "mr": "आठवड्याचा धंदा धोक्यात"},
    "kpi.risk.some": {"hi": "{n} नियमित ग्राहक नहीं आ रहे", "en": "{n} regular customers stopped coming", "mr": "{n} नियमित ग्राहक येत नाहीत"},
    "kpi.risk.none": {"hi": "सभी नियमित ग्राहक आ रहे हैं", "en": "All regular customers are coming", "mr": "सर्व नियमित ग्राहक येत आहेत"},
    "kpi.empty.sales": {"hi": "अभी बिक्री का डेटा नहीं है", "en": "No sales data yet", "mr": "अजून विक्रीचा डेटा नाही"},
    "kpi.empty.risk": {"hi": "डेटा आने पर दिखेगा", "en": "Shown once data arrives", "mr": "डेटा आल्यावर दिसेल"},
    "per_week": {"hi": "/हफ़्ता", "en": "/week", "mr": "/आठवडा"},
    # insights
    "lapsed.title": {"hi": "{n} नियमित ग्राहक आना बंद हो गए", "en": "{n} regular customers stopped coming",
                     "mr": "{n} नियमित ग्राहकांनी येणे बंद केले"},
    "lapsed.detail": {"hi": "{names} पहले हफ़्ते में 3-4 बार आते थे, अब {a}-{b} दिन से नहीं आए।",
                      "en": "{names} used to come 3-4 times a week; not seen for {a}-{b} days.",
                      "mr": "{names} आधी आठवड्यात 3-4 वेळा येत होते, आता {a}-{b} दिवसांपासून आले नाहीत."},
    "lapsed.impact": {"hi": "धंधा खतरे में", "en": "business at risk", "mr": "धंदा धोक्यात"},
    "lapsed.action": {"hi": "कौन नहीं आ रहा?", "en": "Who stopped coming?", "mr": "कोण येत नाही?"},
    "lapsed.q": {"hi": "कौन से पुराने ग्राहक आना बंद हो गए?", "en": "Which regular customers stopped coming?",
                 "mr": "कोणत्या जुन्या ग्राहकांनी येणे बंद केले?"},
    "overdue.title": {"hi": "{name} का उधार {d} दिन से रुका है", "en": "{name}'s udhaar unpaid for {d} days",
                      "mr": "{name} यांची उधारी {d} दिवसांपासून थकली आहे"},
    "overdue.detail": {"hi": "{amt} बाकी, आखिरी भुगतान {date} को आया था।", "en": "{amt} outstanding, last payment on {date}.",
                       "mr": "{amt} बाकी, शेवटचा भरणा {date} रोजी आला होता."},
    "overdue.detail_none": {"hi": "{amt} बाकी, अभी तक कोई भुगतान नहीं।", "en": "{amt} outstanding, no payment yet.",
                            "mr": "{amt} बाकी, अजून भरणा नाही."},
    "overdue.impact": {"hi": "वापस आ सकता है", "en": "can be recovered", "mr": "परत मिळू शकतात"},
    "overdue.action": {"hi": "रिमाइंडर भेजें", "en": "Send reminder", "mr": "रिमाइंडर पाठवा"},
    "overdue.q": {"hi": "{first} को पेमेंट का रिमाइंडर भेज दो", "en": "Send {first} a payment reminder",
                  "mr": "{first} ला पेमेंटचा रिमाइंडर पाठव"},
    "dip.title": {"hi": "{wd} हर हफ़्ते {p}% धीमा", "en": "{wd} is {p}% slower every week", "mr": "{wd} दर आठवड्याला {p}% कमी"},
    "dip.detail": {"hi": "{wd} की औसत बिक्री {a}, बाकी दिनों की {b}। उस दिन कोई छोटा ऑफ़र या कॉम्बो आज़माइए।",
                   "en": "Average {wd} sales are {a}, against {b} on other days. Try a small offer or combo that day.",
                   "mr": "{wd} ची सरासरी विक्री {a}, बाकी दिवसांची {b}. त्या दिवशी छोटी ऑफर किंवा कॉम्बो करून पहा."},
    "dip.impact": {"hi": "अगर बाकी दिनों जैसा हो", "en": "if it matched other days", "mr": "इतर दिवसांसारखा झाला तर"},
    "dip.action": {"hi": "{wd} का हाल", "en": "{wd} details", "mr": "{wd} चा आढावा"},
    "dip.q": {"hi": "{wd} को धंधा कैसा गया?", "en": "How was business on {wd}?", "mr": "{wd} ला धंदा कसा झाला?"},
    "drop.title": {"hi": "{cat} की बिक्री {p}% गिरी", "en": "{cat}: sales down {p}%", "mr": "{cat} विक्री {p}% घटली"},
    "drop.detail": {"hi": "पिछले 14 दिन में {s}, उससे पहले के 14 दिन से {lost} कम।{link}",
                    "en": "{s} in the last 14 days, {lost} less than the 14 days before.{link}",
                    "mr": "मागील 14 दिवसांत {s}, त्याआधीच्या 14 दिवसांपेक्षा {lost} कमी.{link}"},
    "drop.link": {"hi": " जो नियमित ग्राहक आना बंद हुए, वही सबसे ज़्यादा किराना लेते थे। उन्हें वापस लाना इसका सबसे बड़ा इलाज है।",
                  "en": " The regulars who stopped coming bought the most groceries; winning them back is the biggest fix.",
                  "mr": " ज्या नियमित ग्राहकांनी येणे बंद केले, तेच सर्वात जास्त किराणा घेत होते. त्यांना परत आणणे हाच मोठा उपाय आहे."},
    "drop.impact": {"hi": "14 दिन में कम", "en": "less in 14 days", "mr": "14 दिवसांत कमी"},
    "drop.action": {"hi": "क्या नहीं बिक रहा?", "en": "What's not selling?", "mr": "काय विकले जात नाही?"},
    "drop.q": {"hi": "क्या चीज़ नहीं बिक रही है?", "en": "What is not selling?", "mr": "कोणत्या वस्तू विकल्या जात नाहीत?"},
    "slow.title": {"hi": "{p} लगभग पड़ा है", "en": "{p} is barely selling", "mr": "{p} जवळजवळ पडून आहे"},
    "slow.detail": {"hi": "14 दिन में सिर्फ़ {u} बिके। अगली बार कम मँगाइए या किसी तेज़ बिकने वाली चीज़ के साथ कॉम्बो बनाइए।",
                    "en": "Only {u} sold in 14 days. Order less next time, or bundle it with a fast seller.",
                    "mr": "14 दिवसांत फक्त {u} विकले. पुढच्या वेळी कमी मागवा किंवा जलद विकणाऱ्या वस्तूसोबत कॉम्बो करा."},
    "slow.units": {"hi": "{u} नग", "en": "{u} units", "mr": "{u} नग"},
    "slow.impact": {"hi": "14 दिन में", "en": "in 14 days", "mr": "14 दिवसांत"},
    "slow.action": {"hi": "धीमी चीज़ें देखें", "en": "See slow items", "mr": "हळू वस्तू पहा"},
    "peak.title": {"hi": "सबसे ज़्यादा बिक्री {start} से {end} बजे तक", "en": "Busiest time: {start} to {end}",
                   "mr": "सर्वाधिक विक्री {start} ते {end} वाजेपर्यंत"},
    "peak.detail": {"hi": "पिछले 4 हफ़्तों की {s}% बिक्री सिर्फ़ इन 2 घंटों में। इस समय से पहले स्टॉक और छुट्टे तैयार रखिए।",
                    "en": "{s}% of the last 4 weeks' sales happen in these 2 hours. Have stock and change ready before then.",
                    "mr": "मागील 4 आठवड्यांची {s}% विक्री फक्त या 2 तासांत. त्याआधी माल आणि सुटे पैसे तयार ठेवा."},
    "peak.impact": {"hi": "बिक्री 2 घंटे में", "en": "of sales in 2 hours", "mr": "विक्री 2 तासांत"},
    "top.title": {"hi": "{name} आपके सबसे बड़े ग्राहक", "en": "{name} is your biggest customer", "mr": "{name} तुमचे सर्वात मोठे ग्राहक"},
    "top.detail": {"hi": "पिछले 30 दिन में {amt} की खरीदारी। एक छोटा धन्यवाद या छूट वफ़ादारी बढ़ाएगी।",
                   "en": "{amt} spent in the last 30 days. A small thank-you or discount builds loyalty.",
                   "mr": "मागील 30 दिवसांत {amt} ची खरेदी. एक छोटा धन्यवाद किंवा सूट निष्ठा वाढवेल."},
    "top.impact": {"hi": "30 दिन में", "en": "in 30 days", "mr": "30 दिवसांत"},
    # briefing
    "brief.head": {"hi": "नमस्ते {owner} जी। आज {s} की बिक्री हुई, {n} पेमेंट आए", "en": "Namaste {owner}. Today's sales: {s} from {n} payments",
                   "mr": "नमस्कार {owner}. आज {s} ची विक्री झाली, {n} पेमेंट आले"},
    "brief.delta": {"hi": ", पिछले {wd} से {p}% {dir}", "en": ", {p}% {dir} than last {wd}", "mr": ", मागील {wd} पेक्षा {p}% {dir}"},
    "brief.more": {"hi": "ज़्यादा", "en": "higher", "mr": "जास्त"},
    "brief.less": {"hi": "कम", "en": "lower", "mr": "कमी"},
    "brief.end": {"hi": "।", "en": ".", "mr": "."},
    "brief.pending": {"hi": "{name} ने {amt} UPI से भेजे हैं; क्या यह उधार की वापसी है? ऊपर हाँ या ना दबाइए।",
                      "en": "{name} sent {amt} by UPI. Is it an udhaar repayment? Tap Yes or No above.",
                      "mr": "{name} यांनी {amt} UPI ने पाठवले आहेत; ही उधारीची परतफेड आहे का? वर हो किंवा नाही दाबा."},
    "brief.count": {"hi": "{n} ज़रूरी बातें।", "en": "{n} important things.", "mr": "{n} महत्त्वाच्या गोष्टी."},
    "brief.ord": {"hi": "पहली|दूसरी|तीसरी", "en": "First|Second|Third", "mr": "पहिली|दुसरी|तिसरी"},
    "brief.empty": {"hi": "नमस्ते {owner} जी। अभी आपकी दुकान की बिक्री का डेटा नहीं आया है। जैसे ही UPI पेमेंट आएँगे, मैं रोज़ की ब्रीफ़िंग और सुझाव दिखाऊँगा।",
                    "en": "Namaste {owner}. No sales data has arrived for your shop yet. As soon as UPI payments come in, I'll show a daily briefing and suggestions.",
                    "mr": "नमस्कार {owner}. अजून तुमच्या दुकानाचा विक्रीचा डेटा आलेला नाही. UPI पेमेंट येताच मी रोजची ब्रीफिंग आणि सूचना दाखवेन."},
    # tool messages shown directly in answer cards
    "tool.no_udhaar": {"hi": "'{name}' के नाम पर कोई उधार बाकी नहीं है।", "en": "No udhaar outstanding for '{name}'.",
                       "mr": "'{name}' यांच्या नावावर कोणतीही उधारी बाकी नाही."},
    "tool.bad_date": {"hi": "'{d}' कौन-सा दिन है, समझ नहीं आया। जैसे 'आज', 'कल', 'मंगलवार' या '15 सितंबर' बोलिए।",
                      "en": "I couldn't tell which day '{d}' is. Try 'today', 'yesterday', 'Tuesday' or '15 September'.",
                      "mr": "'{d}' कोणता दिवस आहे ते समजले नाही. 'आज', 'काल', 'मंगळवार' किंवा '15 सप्टेंबर' असे सांगा."},
    "tool.no_data_for": {"hi": "{d} का कोई हिसाब नहीं है। हिसाब {start} से {end} तक का है।",
                         "en": "There are no sales records for {d}. Records cover {start} to {end}.",
                         "mr": "{d} चा कोणताही हिशोब नाही. हिशोब {start} ते {end} पर्यंतचा आहे."},
    "tool.future_date": {"hi": "{d} अभी आया नहीं है, उसका हिसाब नहीं हो सकता।", "en": "{d} hasn't happened yet, so there's nothing to show.",
                         "mr": "{d} अजून आलेला नाही, त्याचा हिशोब नाही."},
    "tool.did_you_mean": {"hi": "'{name}' नाम नहीं मिला। क्या आपका मतलब {sug} है?", "en": "I couldn't find '{name}'. Did you mean {sug}?",
                          "mr": "'{name}' नाव सापडले नाही. तुम्हाला {sug} म्हणायचे आहे का?"},
    "tool.ambiguous": {"hi": "इस नाम के कई ग्राहक हैं: {names}। पूरा नाम बताइए।", "en": "Several customers match: {names}. Please say the full name.",
                       "mr": "या नावाचे अनेक ग्राहक आहेत: {names}. पूर्ण नाव सांगा."},
    "done.reminder": {"hi": "हो गया, {name} को Telegram पर रिमाइंडर भेज दिया।", "en": "Done, the reminder was sent to {name} on Telegram.",
                      "mr": "झाले, {name} यांना Telegram वर रिमाइंडर पाठवला."},
    "done.udhaar": {"hi": "खाते में लिख दिया: {name}, {amt}। अब बाकी {after}।", "en": "Saved in the khata: {name}, {amt}. Now {after} is outstanding.",
                    "mr": "खात्यात लिहिले: {name}, {amt}. आता {after} बाकी."},
    "done.upi": {"hi": "हो गया, {name} का UPI पेमेंट तय कर दिया।", "en": "Done, {name}'s UPI payment has been recorded.",
                 "mr": "झाले, {name} यांचे UPI पेमेंट नोंदवले."},
    "fb.summary": {"hi": "{day} {amt} की बिक्री हुई, {n} पेमेंट आए।", "en": "{day}: sales of {amt} from {n} payments.",
                   "mr": "{day} {amt} ची विक्री झाली, {n} पेमेंट आले."},
    "fb.today": {"hi": "आज", "en": "Today", "mr": "आज"},
    "fb.udhaar_one": {"hi": "{name} का {amt} उधार बाकी है।", "en": "{name} owes {amt}.", "mr": "{name} यांची {amt} उधारी बाकी आहे."},
    "fb.udhaar_all": {"hi": "कुल {total} उधार बाकी है, {n} ग्राहकों पर। सबसे ज़्यादा: {top}।", "en": "Total udhaar is {total} across {n} customers. Biggest: {top}.",
                      "mr": "एकूण {total} उधारी बाकी आहे, {n} ग्राहकांवर. सर्वात जास्त: {top}."},
    "fb.lapsed": {"hi": "{names} आना बंद हो गए हैं। हफ़्ते का करीब {amt} का धंधा खतरे में है।", "en": "{names} have stopped coming. About {amt} a week is at risk.",
                  "mr": "{names} येणे बंद झाले आहे. आठवड्याचा सुमारे {amt} चा धंदा धोक्यात आहे."},
    "fb.lapsed_none": {"hi": "कोई नियमित ग्राहक आना बंद नहीं हुआ है।", "en": "No regular customer has stopped coming.", "mr": "कोणत्याही नियमित ग्राहकाने येणे बंद केलेले नाही."},
    "fb.product": {"hi": "सबसे कम बिक्री {cat} की है ({p}%)। धीमी चीज़ें: {items}।", "en": "{cat} sells the least ({p}% of sales). Slow items: {items}.",
                   "mr": "सर्वात कमी विक्री {cat} ची आहे ({p}%). हळू वस्तू: {items}."},
    "fb.upi": {"hi": "{n} UPI पेमेंट पर फ़ैसला बाकी है।", "en": "{n} UPI payments are waiting for your decision.", "mr": "{n} UPI पेमेंटवर निर्णय बाकी आहे."},
    "tool.telegram_only": {"hi": "अभी रिमाइंडर सिर्फ़ Telegram पर भेजे जा सकते हैं।", "en": "Reminders can only be sent on Telegram right now.",
                           "mr": "सध्या रिमाइंडर फक्त Telegram वर पाठवता येतात."},
    "tool.opt_out": {"hi": "{name} ने रिमाइंडर बंद करवाए हैं, इसलिए रिमाइंडर नहीं भेजा जा सकता।",
                     "en": "{name} has opted out of reminders, so none can be sent.",
                     "mr": "{name} यांनी रिमाइंडर बंद केले आहेत, त्यामुळे रिमाइंडर पाठवता येणार नाही."},
    "tool.cooldown": {"hi": "{name} को {when} रिमाइंडर जा चुका है। {h} घंटे में दोबारा नहीं भेजते।",
                      "en": "{name} was already reminded {when}. Reminders aren't repeated within {h} hours.",
                      "mr": "{name} यांना {when} रिमाइंडर गेला आहे. {h} तासांत पुन्हा पाठवत नाही."},
    "tool.just_now": {"hi": "अभी-अभी", "en": "just now", "mr": "आत्ताच"},
    "tool.hours_ago": {"hi": "{h} घंटे पहले", "en": "{h} hours ago", "mr": "{h} तासांपूर्वी"},
    "tool.tg_not_set": {"hi": "Telegram अभी सेट नहीं है, इसलिए रिमाइंडर नहीं भेजा जा सकता। रिमाइंडर सिर्फ़ Telegram से जाते हैं; कोई और तरीका नहीं है।",
                        "en": "Telegram isn't set up, so the reminder can't be sent. Reminders go only through Telegram; there is no other channel.",
                        "mr": "Telegram सेट केलेले नाही, त्यामुळे रिमाइंडर पाठवता येणार नाही. रिमाइंडर फक्त Telegram ने जातात; दुसरा मार्ग नाही."},
    "tool.reminder_failed": {"hi": "रिमाइंडर नहीं गया: {e}", "en": "Reminder not sent: {e}", "mr": "रिमाइंडर गेला नाही: {e}"},
    "tool.kind": {"hi": "kind 'payment' या 'credit' होना चाहिए", "en": "kind must be 'payment' or 'credit'", "mr": "kind 'payment' किंवा 'credit' असावा"},
    "tool.bad_amount": {"hi": "रकम समझ नहीं आई: {a}", "en": "Couldn't understand the amount: {a}", "mr": "रक्कम समजली नाही: {a}"},
    "tool.amount_range": {"hi": "रकम ₹1 से {max} के बीच होनी चाहिए।", "en": "The amount must be between ₹1 and {max}.",
                          "mr": "रक्कम ₹1 ते {max} दरम्यान असावी."},
    "tool.need_name": {"hi": "ग्राहक का नाम बताइए।", "en": "Please tell me the customer's name.", "mr": "ग्राहकाचे नाव सांगा."},
    "tool.no_customer": {"hi": "'{name}' नाम का कोई उधार वाला ग्राहक नहीं मिला।", "en": "No udhaar customer named '{name}' was found.",
                         "mr": "'{name}' नावाचा उधारीचा ग्राहक सापडला नाही."},
    "tool.overpay": {"hi": "{name} का सिर्फ़ {cur} बाकी है, {amt} ज़्यादा है।", "en": "{name} owes only {cur}; {amt} is more than that.",
                     "mr": "{name} यांचे फक्त {cur} बाकी आहे, {amt} जास्त आहे."},
    "tool.no_pending_upi": {"hi": "'{name}' का कोई UPI पेमेंट फ़ैसले के लिए बाकी नहीं है।", "en": "There's no UPI payment from '{name}' waiting for a decision.",
                            "mr": "'{name}' यांचे कोणतेही UPI पेमेंट निर्णयासाठी बाकी नाही."},
    "tool.already_decided": {"hi": "यह पेमेंट पहले ही तय हो चुका है ({e})।", "en": "This payment was already decided ({e}).",
                             "mr": "हे पेमेंट आधीच ठरले आहे ({e})."},
}


def t(key: str, lang: str | None = None, **kw) -> str:
    lang = lang_or_default(lang or current_lang.get())
    entry = T[key]
    return (entry.get(lang) or entry["hi"]).format(**kw)
