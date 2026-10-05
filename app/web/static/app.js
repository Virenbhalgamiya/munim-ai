"use strict";
// मुनीम AI web app: login (OTP / developer mode), proactive dashboard, voice assistant.
// Interface languages: हिंदी (hi) · English (en) · मराठी (mr) — one switch changes everything.
// No external libraries (strict CSP); charts are hand-built SVG. All data text goes through
// textContent (never innerHTML) so customer names can't inject markup.

const APP_VERSION = "1.6.0";                      // keep in sync with APP_VERSION in app/main.py
const $ = (id) => document.getElementById(id);
const SVGNS = "http://www.w3.org/2000/svg";
const fmt = (n) => "₹" + Math.round(n).toLocaleString("en-IN");
const fmtK = (n) => n >= 1000 ? "₹" + (Math.round(n / 100) / 10).toString().replace(/\.0$/, "") + "k" : "₹" + Math.round(n);
const WEEK = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

// ------------------------------------------------------------------ translations
const DAYS = {
  hi: ["सोमवार", "मंगलवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार", "रविवार"],
  mr: ["सोमवार", "मंगळवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार", "रविवार"],
  en: WEEK,
};
const DAYS_SHORT = {
  hi: ["सोम", "मंगल", "बुध", "गुरु", "शुक्र", "शनि", "रवि"],
  mr: ["सोम", "मंगळ", "बुध", "गुरु", "शुक्र", "शनि", "रवि"],
  en: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
};
const SUGGESTIONS = {
  hi: ["आज का हिसाब कैसा रहा?", "किसका कितना उधार बाकी है?", "कौन से पुराने ग्राहक आना बंद हो गए?", "क्या चीज़ नहीं बिक रही है?", "सुरेश ने 500 रुपये दिए", "मंगलवार को धंधा कैसा गया?"],
  en: ["How were sales today?", "Who owes how much udhaar?", "Which regular customers stopped coming?", "What is not selling?", "Suresh paid 500 rupees", "How was business on Tuesday?"],
  mr: ["आजचा हिशोब कसा झाला?", "कोणाची किती उधारी बाकी आहे?", "कोणत्या जुन्या ग्राहकांनी येणे बंद केले?", "कोणत्या वस्तू विकल्या जात नाहीत?", "सुरेशने 500 रुपये दिले", "मंगळवारी धंदा कसा झाला?"],
};
const LANG_NAME = { hi: { hi: "हिंदी", en: "Hindi", mr: "हिंदी" }, en: { hi: "अंग्रेज़ी", en: "English", mr: "इंग्रजी" }, mr: { hi: "मराठी", en: "Marathi", mr: "मराठी" } };
const I18N = {
  "brand": ["मुनीम", "Munim", "मुनीम"],
  "title": ["मुनीम AI", "Munim AI", "मुनीम AI"],
  "login.h1a": ["आपकी दुकान का", "Your shop's", "तुमच्या दुकानाचा"],
  "login.h1b": ["समझदार मुनीम।", "smart accountant.", "हुशार मुनीम."],
  "login.p": ["बोलकर पूछिए — बिक्री, उधार, कौन-सा ग्राहक नहीं आ रहा। मुनीम खुद बताता है कि आज क्या ज़रूरी है।",
    "Just ask out loud: sales, udhaar, which customer stopped coming. Munim tells you what matters today.",
    "बोलून विचारा — विक्री, उधारी, कोणता ग्राहक येत नाही. मुनीम स्वतः सांगतो आज काय महत्त्वाचे आहे."],
  "login.pt1": ["हिंदी, हिंग्लिश, मराठी और अंग्रेज़ी में बात", "Talk in Hindi, Hinglish, Marathi or English", "हिंदी, हिंग्लिश, मराठी आणि इंग्रजीत बोला"],
  "login.pt2": ["रोज़ की ब्रीफ़िंग और रुपये में सुझाव", "Daily briefing and suggestions in rupees", "रोजची ब्रीफिंग आणि रुपयांत सूचना"],
  "login.pt3": ["आवाज़ से उधार खाता और रिमाइंडर", "Udhaar khata and reminders by voice", "आवाजाने उधारी खाते आणि रिमाइंडर"],
  "login.title": ["लॉगिन", "Log in", "लॉगिन"],
  "login.hint.otp": ["अपने रजिस्टर्ड मोबाइल नंबर पर OTP पाइए", "Get an OTP on your registered mobile number", "तुमच्या नोंदणीकृत मोबाईल नंबरवर OTP मिळवा"],
  "login.hint.pin": ["मोबाइल नंबर और PIN डालिए", "Enter your mobile number and PIN", "मोबाईल नंबर आणि PIN टाका"],
  "login.phone": ["मोबाइल नंबर", "Mobile number", "मोबाईल नंबर"],
  "login.phone.ph": ["10 अंकों का नंबर", "10-digit number", "10 अंकी नंबर"],
  "login.send": ["OTP भेजें", "Send OTP", "OTP पाठवा"],
  "login.verify": ["वेरिफ़ाई करके लॉगिन", "Verify and log in", "पडताळून लॉगिन"],
  "login.pinbtn": ["लॉगिन", "Log in", "लॉगिन"],
  "login.resend": ["दोबारा भेजें", "Resend", "पुन्हा पाठवा"],
  "login.resend_in": ["दोबारा भेजें ({s} सेकंड)", "Resend ({s}s)", "पुन्हा पाठवा ({s} सेकंद)"],
  "login.change": ["नंबर बदलें", "Change number", "नंबर बदला"],
  "login.sent": ["OTP SMS भेज दिया गया ({m} मिनट तक मान्य)।", "OTP sent by SMS (valid for {m} minutes).", "OTP SMS पाठवला ({m} मिनिटे वैध)."],
  "login.loggedout": ["लॉगआउट हो गए।", "Logged out.", "लॉगआउट झाले."],
  "login.expired": ["सेशन खत्म हो गया, फिर से लॉगिन कीजिए।", "Session expired, please log in again.", "सत्र संपले, पुन्हा लॉगिन करा."],
  "top.dev": ["डेवलपर मोड", "Developer mode", "डेव्हलपर मोड"],
  "top.demo": ["डेमो दुकान", "Demo shop", "डेमो दुकान"],
  "top.tg_demo": ["Telegram (डेमो)", "Telegram (demo)", "Telegram (डेमो)"],
  "card.rem_sent_demo": ["✓ भेजा गया (डेमो: असली मैसेज नहीं जाता)", "✓ Sent (demo: no real message is sent)", "✓ पाठवले (डेमो: खरा मेसेज जात नाही)"],
  "top.tg_on": ["Telegram जुड़ा है", "Telegram connected", "Telegram जोडले आहे"],
  "top.tg_off": ["Telegram सेट नहीं", "Telegram not set up", "Telegram सेट नाही"],
  "top.logout": ["लॉगआउट", "Log out", "लॉगआउट"],
  "hero.brief": ["आज की ब्रीफ़िंग", "Today's briefing", "आजची ब्रीफिंग"],
  "hero.hello": ["नमस्ते, {name} जी", "Namaste, {name}", "नमस्कार, {name}"],
  "hero.loading": ["ब्रीफ़िंग तैयार हो रही है…", "Preparing your briefing…", "ब्रीफिंग तयार होत आहे…"],
  "hero.listen": ["ब्रीफ़िंग सुनें", "Listen to briefing", "ब्रीफिंग ऐका"],
  "hero.stop": ["रोकें", "Stop", "थांबवा"],
  "err.title": ["डैशबोर्ड लोड नहीं हुआ", "The dashboard didn't load", "डॅशबोर्ड लोड झाला नाही"],
  "err.retry": ["फिर से कोशिश करें", "Try again", "पुन्हा प्रयत्न करा"],
  "err.nodata": ["डैशबोर्ड का डेटा नहीं मिला।", "Dashboard data unavailable.", "डॅशबोर्डचा डेटा मिळाला नाही."],
  "err.server": ["सर्वर से जुड़ नहीं पाया। क्या सर्वर चल रहा है?", "Can't reach the server. Is it running?", "सर्व्हरशी जोडता आले नाही. सर्व्हर चालू आहे का?"],
  "err.http": ["त्रुटि {s}", "Error {s}", "त्रुटी {s}"],
  "banner.outdated": ["सर्वर पुराना चल रहा है। टर्मिनल में सर्वर बंद करके (Ctrl+C) फिर से चलाइए: python -m app.serve",
    "The server is running an old version. Stop it in the terminal (Ctrl+C) and run: python -m app.serve",
    "सर्व्हर जुनी आवृत्ती चालवत आहे. टर्मिनलमध्ये बंद करा (Ctrl+C) आणि पुन्हा चालवा: python -m app.serve"],
  "banner.refresh": ["ऐप अपडेट हो गया है — पेज रीफ़्रेश कीजिए (F5)।", "The app was updated: please refresh the page (F5).", "ॲप अपडेट झाले आहे: पेज रिफ्रेश करा (F5)."],
  "pay.title": ["UPI से आया पैसा", "Money received by UPI", "UPI ने आलेले पैसे"],
  "pay.sub": ["क्या यह उधार की वापसी है? आप तय कीजिए।", "Is it an udhaar repayment? You decide.", "ही उधारीची परतफेड आहे का? तुम्ही ठरवा."],
  "pay.sent": ["💰 {name} ने {amt} UPI से भेजे", "💰 {name} sent {amt} by UPI", "💰 {name} यांनी {amt} UPI ने पाठवले"],
  "pay.meta": ["आज {time} · इनका {owe} उधार बाकी है। {effect}", "Today {time} · owes {owe} udhaar. {effect}", "आज {time} · यांची {owe} उधारी बाकी आहे. {effect}"],
  "pay.effect": ["हाँ दबाने पर बाकी उधार {after} रह जाएगा।", "If yes, {after} udhaar will remain.", "हो दाबल्यास {after} उधारी उरेल."],
  "pay.effect_over": ["{link} उधार में जमा होगा (खाता पूरा साफ़), बाकी {extra} खरीदारी मानी जाएगी।",
    "{link} clears the udhaar fully; the other {extra} counts as a purchase.",
    "{link} उधारीत जमा होईल (खाते पूर्ण साफ), बाकी {extra} खरेदी मानली जाईल."],
  "pay.yes": ["हाँ, उधार में जमा करें", "Yes, it's a repayment", "हो, उधारीत जमा करा"],
  "pay.no": ["नहीं, यह खरीदारी थी", "No, it was a purchase", "नाही, ती खरेदी होती"],
  "pay.linked": ["✓ {name}: {amt} उधार में जमा, अब {after} बाकी", "✓ {name}: {amt} applied to udhaar, {after} left", "✓ {name}: {amt} उधारीत जमा, आता {after} बाकी"],
  "pay.dismissed": ["✓ {name} का {amt} बिक्री में जोड़ा", "✓ {name}'s {amt} added to sales", "✓ {name} यांचे {amt} विक्रीत जोडले"],
  "sim.toggle": ["🧪 डेमो: UPI पेमेंट आने दें", "🧪 Demo: receive a UPI payment", "🧪 डेमो: UPI पेमेंट येऊ द्या"],
  "sim.note": ["डेमो (सिर्फ़ डेवलपर मोड): मान लीजिए किसी ग्राहक ने अभी UPI से पैसे भेजे।", "Demo (developer mode only): pretend a customer just sent money by UPI.",
    "डेमो (फक्त डेव्हलपर मोड): समजा एखाद्या ग्राहकाने आत्ता UPI ने पैसे पाठवले."],
  "sim.send": ["पेमेंट भेजें", "Send payment", "पेमेंट पाठवा"],
  "sim.done": ["💰 {name} ने {amt} भेजे (डेमो)", "💰 {name} sent {amt} (demo)", "💰 {name} यांनी {amt} पाठवले (डेमो)"],
  "sim.nodebt": ["इस ग्राहक पर उधार नहीं है, पेमेंट बिक्री में गया", "This customer owes nothing; recorded as a sale", "या ग्राहकावर उधारी नाही, पेमेंट विक्रीत गेले"],
  "insights.title": ["आज के सुझाव", "Today's insights", "आजच्या सूचना"],
  "sev.critical": ["ज़रूरी", "Urgent", "तातडीचे"],
  "sev.warning": ["ध्यान दें", "Watch", "लक्ष द्या"],
  "sev.opportunity": ["मौका", "Opportunity", "संधी"],
  "sev.info": ["जानकारी", "Info", "माहिती"],
  "insights.none.title": ["सब ठीक चल रहा है", "All good", "सर्व ठीक चालले आहे"],
  "insights.none": ["अभी कोई ज़रूरी बात नहीं। नया डेटा आते ही सुझाव यहाँ दिखेंगे।", "Nothing urgent right now. Suggestions appear here as new data comes in.",
    "सध्या काही तातडीचे नाही. नवीन डेटा आल्यावर सूचना इथे दिसतील."],
  "chart.sales.title": ["पिछले 28 दिन की बिक्री", "Sales, last 28 days", "मागील 28 दिवसांची विक्री"],
  "chart.sales.sub": ["हर पट्टी एक दिन की कुल बिक्री है। नारंगी पट्टियाँ {wd} हैं — हर हफ़्ते सबसे कम।",
    "Each bar is one day's total. Orange bars are {wd}s, the lowest day every week.",
    "प्रत्येक पट्टी एका दिवसाची एकूण विक्री. नारंगी पट्ट्या {wd} — दर आठवड्याला सर्वात कमी."],
  "chart.legend.today": ["आज", "Today", "आज"],
  "chart.legend.rest": ["बाकी दिन", "Other days", "बाकी दिवस"],
  "chart.week.title": ["हफ़्ते का पैटर्न", "Weekly pattern", "आठवड्याचा पॅटर्न"],
  "chart.week.sub": ["हर दिन की औसत बिक्री (8 हफ़्ते)। नारंगी = सबसे धीमा दिन।", "Average sales per weekday (8 weeks). Orange = slowest day.",
    "प्रत्येक दिवसाची सरासरी विक्री (8 आठवडे). नारंगी = सर्वात कमी दिवस."],
  "chart.cat.title": ["क्या बिक रहा है", "What's selling", "काय विकले जात आहे"],
  "chart.cat.sub": ["पिछले 14 दिन में हर श्रेणी का हिस्सा। नारंगी = सबसे कम हिस्सा। ▲▼ = पिछले 14 दिन से बदलाव।",
    "Share of each category in the last 14 days. Orange = smallest share. ▲▼ = change vs the 14 days before.",
    "मागील 14 दिवसांत प्रत्येक श्रेणीचा वाटा. नारंगी = सर्वात कमी वाटा. ▲▼ = आधीच्या 14 दिवसांपेक्षा बदल."],
  "chart.slow": ["सबसे धीमी चीज़ें: ", "Slowest items: ", "सर्वात हळू वस्तू: "],
  "chart.sold": ["{n} बिके", "{n} sold", "{n} विकले"],
  "chart.avg_of": ["{wd} की औसत बिक्री", "Average {wd} sales", "{wd} ची सरासरी विक्री"],
  "chart.today_suffix": [" (आज)", " (today)", " (आज)"],
  "chart.nodata": ["बिक्री का डेटा आने पर यहाँ दिखेगा।", "Shown here once sales data arrives.", "विक्रीचा डेटा आल्यावर इथे दिसेल."],
  "khata.title": ["उधार खाता", "Udhaar khata", "उधारी खाते"],
  "khata.customer": ["ग्राहक", "Customer", "ग्राहक"],
  "khata.due": ["बाकी", "Outstanding", "बाकी"],
  "khata.last": ["आखिरी भुगतान", "Last payment", "शेवटचा भरणा"],
  "khata.summary": ["कुल {total} · {n} ग्राहक", "Total {total} · {n} customers", "एकूण {total} · {n} ग्राहक"],
  "khata.overdue": ["देर से · {d} दिन", "Overdue · {d} days", "थकीत · {d} दिवस"],
  "khata.today": ["आज", "Today", "आज"],
  "khata.never": ["कोई भुगतान नहीं", "No payment yet", "अजून भरणा नाही"],
  "khata.ago": ["{d} दिन पहले", "{d} days ago", "{d} दिवसांपूर्वी"],
  "khata.remind": ["रिमाइंडर", "Reminder", "रिमाइंडर"],
  "khata.remind_q": ["{first} को पेमेंट का रिमाइंडर भेज दो", "Send {first} a payment reminder", "{first} ला पेमेंटचा रिमाइंडर पाठव"],
  "khata.empty": ["कोई उधार बाकी नहीं।", "No udhaar outstanding.", "कोणतीही उधारी बाकी नाही."],
  "asst.title": ["मुनीम से पूछिए", "Ask Munim", "मुनीमला विचारा"],
  "asst.sub": ["बोलकर या लिखकर — किसी भी भाषा में", "Speak or type, in any language", "बोलून किंवा लिहून — कोणत्याही भाषेत"],
  "asst.new": ["नई बातचीत", "New chat", "नवीन संभाषण"],
  "asst.history": ["🕘 पिछली बातचीत", "🕘 Past chats", "🕘 मागील संभाषणे"],
  "asst.history_empty": ["अभी कोई पुरानी बातचीत नहीं है।", "No past chats yet.", "अजून कोणतेही जुने संभाषण नाही."],
  "asst.history_meta": ["{when} · {n} सवाल", "{when} · {n} questions", "{when} · {n} प्रश्न"],
  "asst.history_now": ["अभी चल रही", "current", "चालू"],
  "asst.ph": ["यहाँ लिखिए…", "Type here…", "इथे लिहा…"],
  "asst.welcome": ["माइक दबाइए और पूछिए", "Tap the mic and ask", "माइक दाबा आणि विचारा"],
  "asst.welcome2": ["जैसे “आज का हिसाब कैसा रहा?” या “सुरेश ने 500 रुपये दिए”। हिंग्लिश में भी बोल सकते हैं।",
    "For example “How were sales today?” or “Suresh paid 500 rupees”. Hindi and Hinglish work too.",
    "उदा. “आजचा हिशोब कसा झाला?” किंवा “सुरेशने 500 रुपये दिले”. हिंदी-हिंग्लिशही चालेल."],
  "asst.voice_f": ["आवाज़: महिला", "Voice: female", "आवाज: महिला"],
  "asst.voice_m": ["आवाज़: पुरुष", "Voice: male", "आवाज: पुरुष"],
  "asst.speak": ["बोलकर सुनाएँ", "Speak answers", "उत्तर बोलून सांगा"],
  "asst.thinking": ["मुनीम सोच रहा है", "Munim is thinking", "मुनीम विचार करत आहे"],
  "asst.how": ["🔍 मुनीम ने कैसे पता किया ({n} कदम)", "🔍 How Munim found this ({n} steps)", "🔍 मुनीमने कसे शोधले ({n} पायऱ्या)"],
  "asst.guard": ["🛡 पुष्टि ज़रूरी", "🛡 confirmation needed", "🛡 पुष्टी आवश्यक"],
  "mic.listening": ["सुन रहा हूँ…", "Listening…", "ऐकत आहे…"],
  "mic.none": ["माइक के लिए Chrome या Edge खोलिए। अभी लिखकर पूछिए।", "Open Chrome or Edge to use the mic. You can type for now.", "माइकसाठी Chrome किंवा Edge उघडा. सध्या लिहून विचारा."],
  "mic.denied": ["माइक की अनुमति दीजिए (एड्रेस बार में 🔒)।", "Allow microphone access (🔒 in the address bar).", "माइकची परवानगी द्या (ॲड्रेस बारमध्ये 🔒)."],
  "mic.error": ["माइक में दिक्कत: {e}", "Mic problem: {e}", "माइकमध्ये अडचण: {e}"],
  "mic.silent": ["कुछ सुनाई नहीं दिया, फिर से कोशिश कीजिए।", "Didn't hear anything, please try again.", "काही ऐकू आले नाही, पुन्हा प्रयत्न करा."],
  "voice.replay": ["🔊 सुनें", "🔊 Listen", "🔊 ऐका"],
  "voice.tap": ["आवाज़ सुनने के लिए 🔊 दबाइए", "Tap 🔊 to hear the answer", "आवाज ऐकण्यासाठी 🔊 दाबा"],
  "voice.novoice": ["इस ब्राउज़र में {lang} आवाज़ नहीं है। जवाब स्क्रीन पर पढ़िए या Chrome/Edge इस्तेमाल कीजिए।",
    "This browser has no {lang} voice. Read the answer on screen, or use Chrome/Edge.",
    "या ब्राउझरमध्ये {lang} आवाज नाही. उत्तर स्क्रीनवर वाचा किंवा Chrome/Edge वापरा."],
  "card.sales_of": ["{wd} की बिक्री", "{wd} sales", "{wd} ची विक्री"],
  "card.payments": ["पेमेंट", "Payments", "पेमेंट"],
  "card.vs": ["पिछले 4 {wd} से", "vs last 4 {wd}", "मागील 4 {wd} पेक्षा"],
  "card.week_title": ["हफ़्ते के हर दिन की औसत बिक्री", "Average sales by weekday", "आठवड्याच्या प्रत्येक दिवसाची सरासरी विक्री"],
  "card.leg_sel": ["नीला = {wd} (जिसकी बात हो रही है)", "Blue = {wd} (the day asked about)", "निळा = {wd} (ज्याबद्दल बोलत आहोत)"],
  "card.leg_weak": ["नारंगी = सबसे धीमा दिन ({wd})", "Orange = slowest day ({wd})", "नारंगी = सर्वात कमी दिवस ({wd})"],
  "card.leg_rest": ["धूसर = बाकी दिन", "Grey = other days", "राखाडी = बाकी दिवस"],
  "card.khata_total": ["उधार खाता · कुल {total}", "Udhaar khata · total {total}", "उधारी खाते · एकूण {total}"],
  "card.udhaar": ["उधार", "Udhaar", "उधारी"],
  "card.since": ["{d} दिन से", "{d} days", "{d} दिवसांपासून"],
  "card.risk": ["खतरे में: {amt} हर हफ़्ते", "At risk: {amt} every week", "धोक्यात: दर आठवड्याला {amt}"],
  "card.lapsed_row": ["{d} दिन से नहीं आए · पहले हफ़्ते में {v} बार", "Not seen for {d} days · used to come {v}× a week", "{d} दिवसांपासून आले नाहीत · आधी आठवड्यात {v} वेळा"],
  "card.cat_title": ["श्रेणी का हिस्सा · पिछले {d} दिन", "Category share · last {d} days", "श्रेणीचा वाटा · मागील {d} दिवस"],
  "card.cat_leg": ["नारंगी = सबसे कम बिकने वाली श्रेणी", "Orange = weakest category", "नारंगी = सर्वात कमी विकली जाणारी श्रेणी"],
  "card.slow": ["धीमी चीज़ें: ", "Slow items: ", "हळू वस्तू: "],
  "card.rem_title": ["Telegram रिमाइंडर · {name} · {amt}", "Telegram reminder · {name} · {amt}", "Telegram रिमाइंडर · {name} · {amt}"],
  "card.rem_sent": ["✓ Telegram पर भेज दिया", "✓ Sent on Telegram", "✓ Telegram वर पाठवले"],
  "card.rem_wait": ["अभी नहीं भेजा · “हाँ” कहने पर जाएगा", "Not sent yet · goes when you say “yes”", "अजून पाठवले नाही · “हो” म्हटल्यावर जाईल"],
  "card.rem_fail": ["नहीं भेजा", "Not sent", "पाठवले नाही"],
  "card.paid": ["जमा", "Payment", "जमा"],
  "card.credit": ["उधार", "Credit", "उधारी"],
  "card.newkhata": [" (नया खाता)", " (new khata)", " (नवीन खाते)"],
  "card.ledger_leg": ["पहले का बकाया → बाद का बकाया", "Balance before → after", "आधीची बाकी → नंतरची बाकी"],
  "card.saved": ["✓ खाते में लिख दिया", "✓ Saved to the khata", "✓ खात्यात लिहिले"],
  "card.save_wait": ["अभी नहीं लिखा · “हाँ” कहने पर लिखेगा", "Not saved yet · saves when you say “yes”", "अजून लिहिले नाही · “हो” म्हटल्यावर लिहील"],
  "card.save_fail": ["नहीं लिखा", "Not saved", "लिहिले नाही"],
  "card.upi_title": ["UPI से आया पैसा, फ़ैसला बाकी", "UPI payments awaiting your decision", "UPI ने आलेले पैसे, निर्णय बाकी"],
  "card.upi_owes": ["उधार बाकी {amt}", "Owes {amt}", "उधारी बाकी {amt}"],
  "card.upi_dec": ["UPI पेमेंट · {name} · {amt} → {d}", "UPI payment · {name} · {amt} → {d}", "UPI पेमेंट · {name} · {amt} → {d}"],
  "card.upi_purchase": ["खरीदारी", "purchase", "खरेदी"],
  "card.upi_repay": ["उधार की वापसी", "repayment", "उधारी परतफेड"],
  "card.upi_done_purchase": ["✓ बिक्री में जोड़ा", "✓ Added to sales", "✓ विक्रीत जोडले"],
  "card.upi_done_repay": ["✓ उधार में जमा, अब {after} बाकी", "✓ Applied, {after} left", "✓ उधारीत जमा, आता {after} बाकी"],
  "toast.sent": ["✓ {name} को Telegram पर रिमाइंडर गया", "✓ Reminder sent to {name} on Telegram", "✓ {name} यांना Telegram वर रिमाइंडर गेला"],
  "toast.saved": ["✓ खाता अपडेट: {name} {amt}", "✓ Khata updated: {name} {amt}", "✓ खाते अपडेट: {name} {amt}"],
  "toast.upi": ["✓ {name}: UPI पेमेंट तय हो गया", "✓ {name}: UPI payment decided", "✓ {name}: UPI पेमेंट ठरले"],
};
const LANG_INDEX = { hi: 0, en: 1, mr: 2 };
let LANG = "hi";
function t(key, vars = {}) {
  const row = I18N[key];
  let s = row ? (row[LANG_INDEX[LANG]] ?? row[0]) : key;
  for (const [k, v] of Object.entries(vars)) s = s.split("{" + k + "}").join(String(v));
  return s;
}
const dayName = (en) => DAYS[LANG][WEEK.indexOf(en)] || en;
const dayShort = (en) => DAYS_SHORT[LANG][WEEK.indexOf(en)] || en;
const nameOf = (o) => (LANG === "en" ? (o.customer_name || o.customer_name_hi) : (o.customer_name_hi || o.customer_name)) || "";
const first = (name) => (name || "").split(" ")[0];

function applyStatic() {
  document.documentElement.lang = LANG;
  document.title = t("title");
  document.querySelectorAll("[data-i18n]").forEach((n) => { n.textContent = t(n.dataset.i18n); });
  document.querySelectorAll("[data-i18n-ph]").forEach((n) => { n.placeholder = t(n.dataset.i18nPh); });
  $("uiLang").value = LANG; $("loginLang").value = LANG;
}

let merchant = null, sessionId = null, busy = false, dashboard = null, authCfg = { method: "otp", dev_mode: false, voice_engine: "browser" };

// ------------------------------------------------------------------ DOM helpers
function el(tag, props = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v == null || v === false) continue;
    if (k === "class") e.className = v;
    else if (k === "text") e.textContent = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v);
  }
  for (const c of kids.flat()) if (c != null && c !== false) e.append(c.nodeType ? c : document.createTextNode(String(c)));
  return e;
}
function sv(tag, attrs = {}, ...kids) {
  const e = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v != null) e.setAttribute(k, v);
  for (const c of kids.flat()) if (c != null) e.append(c.nodeType ? c : document.createTextNode(String(c)));
  return e;
}
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* per-viewer convenience only */ } },
};
let bannerKey = null;
function banner(key) { bannerKey = key; $("banner").textContent = key ? t(key) : ""; $("banner").hidden = !key; }

// ------------------------------------------------------------------ API
async function api(path, opts = {}) {
  let r;
  try { r = await fetch(path, { credentials: "same-origin", ...opts }); }
  catch { throw new Error(t("err.server")); }
  let data = {};
  try { data = await r.json(); } catch { /* non-JSON */ }
  if (r.status === 401 && !path.startsWith("/api/otp") && path !== "/api/login") { showLogin(t("login.expired")); throw new Error("Login required"); }
  if (r.status === 404 && path.startsWith("/api/") && data.detail === "Not Found") { banner("banner.outdated"); throw new Error(t("banner.outdated")); }
  if (!r.ok) throw new Error(data.detail || t("err.http", { s: r.status }));
  return data;
}
const postJSON = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });

// ------------------------------------------------------------------ tooltip + toast
function showTip(evt, title, line) {
  const tip = $("tooltip"); tip.replaceChildren(el("b", { text: title }), line ? el("span", { text: line }) : null);
  tip.hidden = false; tip.style.left = evt.clientX + "px"; tip.style.top = evt.clientY + "px";
}
const hideTip = () => { $("tooltip").hidden = true; };
function toast(text) { const tt = $("toast"); tt.textContent = text; tt.hidden = false; clearTimeout(toast._t); toast._t = setTimeout(() => { tt.hidden = true; }, 3500); }

// ------------------------------------------------------------------ language switch
async function setLang(lang, { reload = true } = {}) {
  if (!LANG_INDEX.hasOwnProperty(lang)) lang = "hi";
  LANG = lang; store.set("munim.lang", lang);
  applyStatic();
  setLoginStep(authCfg.method === "pin" ? "pin" : (otpSent ? "otp" : "phone"), { keepFocus: true });
  if (bannerKey) banner(bannerKey);
  if ($("welcome")) $("welcome").replaceWith(welcome());
  if (!SR) $("live").textContent = t("mic.none");
  if (merchant) {
    renderSuggestions();
    setTopbar();
    if (!$("history").hidden) renderHistory();
    if (reload) await loadDashboard();
  }
}
$("uiLang").onchange = () => setLang($("uiLang").value);
$("loginLang").onchange = () => setLang($("loginLang").value);

// ------------------------------------------------------------------ login
let otpSent = false, resendTimer = null;
function setLoginStep(step, { keepFocus = false } = {}) {           // "phone" | "otp" | "pin"
  otpSent = step === "otp";
  $("pinRow").hidden = step !== "pin"; $("otpRow").hidden = step !== "otp"; $("otpLinks").hidden = step !== "otp";
  $("phone").readOnly = step === "otp";
  $("loginBtn").textContent = t(step === "phone" ? "login.send" : step === "otp" ? "login.verify" : "login.pinbtn");
  $("loginHint").textContent = t(step === "pin" ? "login.hint.pin" : "login.hint.otp");
  if (step === "otp" && !keepFocus) $("otp").focus();
}
function startResendCountdown(seconds) {
  clearInterval(resendTimer);
  let left = seconds;
  const tick = () => {
    $("resendBtn").disabled = left > 0;
    $("resendBtn").textContent = left > 0 ? t("login.resend_in", { s: left }) : t("login.resend");
    if (left-- <= 0) clearInterval(resendTimer);
  };
  tick(); resendTimer = setInterval(tick, 1000);
}
async function sendOtp() {
  $("loginErr").textContent = ""; $("loginInfo").textContent = "";
  const r = await postJSON("/api/otp/send", { phone: $("phone").value.trim() });
  setLoginStep("otp");
  $("loginInfo").textContent = t("login.sent", { m: r.valid_minutes });
  startResendCountdown(r.resend_after);
}
$("loginForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("loginBtn").disabled = true; $("loginErr").textContent = "";
  try {
    let r;
    if (authCfg.method === "pin") r = await postJSON("/api/login", { phone: $("phone").value.trim(), pin: $("pin").value.trim() });
    else if (!otpSent) { await sendOtp(); return; }
    else r = await postJSON("/api/otp/verify", { phone: $("phone").value.trim(), code: $("otp").value.trim() });
    $("otp").value = ""; $("pin").value = ""; $("loginInfo").textContent = ""; clearInterval(resendTimer);
    const me = await api("/api/me");
    await showApp(r.merchant, me.modes);
  } catch (err) {
    $("loginErr").textContent = err.message;
    if (/समय-सीमा|नया OTP|expired/i.test(err.message)) { $("resendBtn").disabled = false; $("resendBtn").textContent = t("login.resend"); }
  } finally { $("loginBtn").disabled = false; }
});
$("resendBtn").onclick = async () => { try { await sendOtp(); } catch (err) { $("loginErr").textContent = err.message; } };
$("changeBtn").onclick = () => { clearInterval(resendTimer); $("otp").value = ""; $("loginInfo").textContent = ""; $("loginErr").textContent = ""; setLoginStep("phone"); $("phone").focus(); };
$("logoutBtn").onclick = async () => {
  try { await api("/api/logout", { method: "POST" }); } catch { /* ignore */ }
  stopSpeaking();
  if (authCfg.dev_mode) { location.reload(); return; }   // developer mode logs straight back in
  showLogin(t("login.loggedout"));
};

function showLogin(msg) {
  merchant = null;
  $("appView").hidden = true; $("loginView").hidden = false;
  $("loginErr").textContent = msg || "";
  setLoginStep(authCfg.method === "pin" ? "pin" : "phone");
  $("phone").focus();
}

let lastModes = null;
function setTopbar() {
  $("storeName").textContent = (LANG === "en" ? merchant.store_name : merchant.store_name_hi) || merchant.store_name;
  $("devPill").hidden = !authCfg.dev_mode;
  $("devPill").textContent = t(authCfg.public_demo ? "top.demo" : "top.dev");
  const tgOn = authCfg.public_demo || (lastModes && !String(lastModes.reminders).includes("not configured"));
  $("tgPill").classList.toggle("on", !!tgOn);
  $("tgText").textContent = t(authCfg.public_demo ? "top.tg_demo" : tgOn ? "top.tg_on" : "top.tg_off");
}

async function showApp(profile, modes) {
  merchant = profile; lastModes = modes;
  $("loginView").hidden = true; $("appView").hidden = false;
  setTopbar();
  sessionId = store.get("munim.session." + profile.id);
  if (!sessionId) { sessionId = "web-" + Math.random().toString(36).slice(2, 12); store.set("munim.session." + profile.id, sessionId); }
  $("voice").value = store.get("munim.voice") || "female";
  renderSuggestions();
  await Promise.all([loadDashboard(), restoreConversation()]);
}

// ------------------------------------------------------------------ dashboard
async function loadDashboard(flashName) {
  try {
    dashboard = await api("/api/dashboard?lang=" + LANG);
    $("dashError").hidden = true;
  } catch (e) {
    if (e.message === "Login required") return;
    $("dashErrorText").textContent = e.message; $("dashError").hidden = false;
    $("briefingText").textContent = t("err.nodata");
    return;
  }
  const d = dashboard;
  $("todayLabel").textContent = d.greeting.date_display;
  $("heroDate").textContent = `${d.greeting.date_display} · ${t("hero.brief")}`;
  $("heroTitle").textContent = t("hero.hello", { name: d.greeting.owner });
  $("briefingText").textContent = d.briefing;
  const weakName = d.weakest_weekday ? dayName(d.weakest_weekday) : "";
  $("legWeak").textContent = weakName;
  $("salesSub").textContent = weakName ? t("chart.sales.sub", { wd: weakName }) : "";
  renderPayments(d.pending_payments || []);
  renderKpis(d.kpis);
  renderInsights(d.insights);
  renderSalesChart(d.sales_28d, d.weakest_weekday);
  renderWeekdayChart(d.weekday_avg, d.weakest_weekday, d.weakest_gap_pct);
  renderCategories(d.categories, d.slow_products);
  renderKhata(d.khata, flashName);
}
$("dashRetry").onclick = () => loadDashboard();

// ------------------------------------------------------------------ incoming UPI payments
function renderPayments(items) {
  $("payments").hidden = !items.length;
  $("paymentCards").replaceChildren(...items.map((p) => {
    const linkable = Math.min(p.amount, p.outstanding), extra = p.amount - linkable;
    const effect = extra > 0 ? t("pay.effect_over", { link: fmt(linkable), extra: fmt(extra) }) : t("pay.effect", { after: fmt(p.outstanding - linkable) });
    return el("article", { class: "card payment-card", "data-payment": String(p.id) },
      el("div", {},
        el("div", { class: "who", text: t("pay.sent", { name: nameOf(p), amt: p.amount_display }) }),
        el("div", { class: "meta", text: t("pay.meta", { time: p.time, owe: p.outstanding_display, effect }) })),
      el("div", { class: "actions" },
        el("button", { class: "btn primary small", type: "button", "data-act": "link", onclick: () => decidePayment(p, "link"), text: t("pay.yes") }),
        el("button", { class: "btn small", type: "button", "data-act": "dismiss", onclick: () => decidePayment(p, "dismiss"), text: t("pay.no") })));
  }));
}
async function decidePayment(p, act) {
  const card = document.querySelector(`[data-payment="${p.id}"]`);
  card?.querySelectorAll("button").forEach((b) => { b.disabled = true; });
  try {
    const r = await postJSON(`/api/payments/${p.id}/${act}`);
    toast(act === "link" ? t("pay.linked", { name: nameOf(p), amt: r.payment.linked_display, after: r.payment.outstanding_after_display })
      : t("pay.dismissed", { name: nameOf(p), amt: p.amount_display }));
    await loadDashboard(p.customer_name);
  } catch (e) { toast("⚠ " + e.message); await loadDashboard(); }
}
$("simToggle").onclick = () => { $("simForm").hidden = !$("simForm").hidden; };
$("simForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const r = await postJSON("/api/dev/simulate-upi", { customer: $("simCustomer").value, amount: parseInt($("simAmount").value, 10) });
    toast(r.payment.status === "pending" ? t("sim.done", { name: nameOf(r.payment), amt: r.payment.amount_display }) : t("sim.nodebt"));
    $("simForm").hidden = true;
    await loadDashboard();
    if (!$("payments").hidden) $("payments").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (err) { toast("⚠ " + err.message); }
});

function renderKpis(kpis) {
  $("kpis").replaceChildren(...kpis.map((k) => {
    const tile = el("article", { class: "card kpi" + (k.status ? " status-" + k.status : "") },
      el("div", { class: "label" }, k.status ? el("span", { class: "status-icon", "aria-hidden": "true", text: k.status === "critical" ? "🔴" : "🟠" }) : null, k.label),
      el("div", { class: "value", text: k.display }));
    if (k.delta_pct != null) {
      const up = k.delta_pct >= 0;
      tile.append(el("div", { class: "delta" }, el("span", { class: "arrow " + (up ? "up" : "down"), text: up ? "▲" : "▼" }),
        `${Math.abs(k.delta_pct).toFixed(0)}% · ${k.delta_label}`));
    }
    if (k.sub) tile.append(el("div", { class: "sub", text: k.sub }));
    if (k.trend) tile.append(sparkline(k.trend, k.trend_label));
    return tile;
  }));
}
function sparkline(values, label) {
  const w = 84, h = 30, max = Math.max(...values), min = Math.min(...values), span = max - min || 1;
  const pts = values.map((v, i) => [i * (w - 8) / Math.max(1, values.length - 1) + 4, h - 4 - (v - min) / span * (h - 8)]);
  const last = pts[pts.length - 1];
  return sv("svg", { class: "spark", viewBox: `0 0 ${w} ${h}`, role: "img", "aria-label": label },
    sv("title", {}, label),
    sv("polyline", { points: pts.map((p) => p.join(",")).join(" "), fill: "none", stroke: "var(--bar)", "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }),
    sv("circle", { cx: last[0], cy: last[1], r: 4, fill: "var(--brand)", stroke: "var(--surface)", "stroke-width": 2 }));
}
function renderInsights(items) {
  if (!items.length) {
    $("insights").replaceChildren(el("article", { class: "card insight info" }, el("h3", { text: t("insights.none.title") }), el("p", { text: t("insights.none") })));
    return;
  }
  $("insights").replaceChildren(...items.map((i) => el("article", { class: "card insight " + i.severity, "data-insight": i.id },
    el("div", { class: "top" }, el("span", { class: "sev", text: t("sev." + i.severity) })),
    el("h3", {}, el("span", { "aria-hidden": "true", text: i.icon }), el("span", { text: i.title })),
    el("p", { text: i.detail }),
    el("div", { class: "bottom" },
      el("div", { class: "impact" }, el("b", { text: i.impact }), el("span", { text: i.impact_label })),
      i.action ? el("button", { class: "btn small", type: "button", onclick: () => askFromDashboard(i.action.question), text: i.action.label + " →" }) : null))));
}
function niceMax(v) {
  const p = Math.pow(10, Math.floor(Math.log10(v || 1))), n = (v || 1) / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p;
}
function barPath(x, y, w, h, r = 4) {           // rounded data-end, square at the baseline
  if (h <= 0) return "";
  r = Math.min(r, w / 2, h);
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}
function axes(svg, { W, H, l, r, top, b, max, ticks = 4 }) {
  const g = sv("g", { class: "grid" }), ax = sv("g", { class: "axis" });
  for (let i = 0; i <= ticks; i++) {
    const v = max / ticks * i, y = top + (H - top - b) * (1 - v / max);
    g.append(sv("line", { x1: l, x2: W - r, y1: y, y2: y }));
    ax.append(sv("text", { x: l - 8, y: y + 4, "text-anchor": "end" }, fmtK(v)));
  }
  svg.append(g, ax);
}
function renderSalesChart(series, weakest) {
  const W = 760, H = 230, l = 48, r = 8, top = 14, b = 30;
  const max = niceMax(Math.max(...series.map((s) => s.total), 1));
  const svg = sv("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": t("chart.sales.title") });
  axes(svg, { W, H, l, r, top, b, max });
  const slot = (W - l - r) / series.length, bw = Math.min(18, slot * 0.66);
  series.forEach((s, i) => {
    const x = l + i * slot + (slot - bw) / 2, h = (H - top - b) * s.total / max, y = H - b - h;
    const isToday = i === series.length - 1, isWeak = s.weekday === weakest;
    svg.append(sv("path", { class: "bar" + (isToday ? " today" : isWeak ? " hl" : ""), d: barPath(x, y, bw, h) }));
    const hit = sv("rect", { class: "bar-hit", x: l + i * slot, y: top, width: slot, height: H - top - b });
    hit.addEventListener("mousemove", (e) => showTip(e, fmt(s.total), `${dayName(s.weekday)}, ${s.label}${isToday ? t("chart.today_suffix") : ""}`));
    hit.addEventListener("mouseleave", hideTip);
    svg.append(hit);
    if (i % 7 === 0 || isToday) svg.append(sv("text", { x: x + bw / 2, y: H - 10, "text-anchor": "middle", fill: "var(--muted)", "font-size": 11 }, isToday ? t("chart.legend.today") : s.label));
  });
  $("salesChart").replaceChildren(svg);
}
function renderWeekdayChart(days, weakest, gap) {
  const W = 420, H = 230, l = 44, r = 6, top = 24, b = 30;
  const max = niceMax(Math.max(...days.map((d) => d.avg), 1));
  const best = days.reduce((a, d) => (d.avg > a.avg ? d : a), days[0]);
  const svg = sv("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": t("chart.week.title") });
  axes(svg, { W, H, l, r, top, b, max });
  const slot = (W - l - r) / days.length, bw = Math.min(24, slot * 0.6);
  days.forEach((d, i) => {
    const x = l + i * slot + (slot - bw) / 2, h = (H - top - b) * d.avg / max, y = H - b - h;
    const isWeak = d.weekday === weakest;
    svg.append(sv("path", { class: "bar" + (isWeak ? " hl" : ""), d: barPath(x, y, bw, h) }));
    const hit = sv("rect", { class: "bar-hit", x: l + i * slot, y: top, width: slot, height: H - top - b });
    hit.addEventListener("mousemove", (e) => showTip(e, fmt(d.avg), t("chart.avg_of", { wd: d.weekday_hi })));
    hit.addEventListener("mouseleave", hideTip);
    svg.append(hit);
    svg.append(sv("text", { x: x + bw / 2, y: H - 10, "text-anchor": "middle", fill: isWeak ? "var(--ink)" : "var(--muted)", "font-size": 11.5, "font-weight": isWeak ? 700 : 400 }, d.short));
    if (isWeak && d.avg) svg.append(sv("text", { class: "dlabel", x: x + bw / 2, y: y - 6, "text-anchor": "middle" }, `−${gap}%`));
    if (d === best && d.avg) svg.append(sv("text", { class: "dlabel", x: x + bw / 2, y: y - 6, "text-anchor": "middle" }, fmtK(d.avg)));
  });
  $("weekdayChart").replaceChildren(svg);
}
function renderCategories(cats, slow) {
  if (!cats.length) {
    $("categoryBars").replaceChildren(el("p", { class: "muted", text: t("chart.nodata") }));
    $("slowList").replaceChildren();
    return;
  }
  const maxShare = Math.max(...cats.map((c) => c.share_pct));
  const weakest = cats.reduce((a, c) => (c.share_pct < a.share_pct ? c : a), cats[0]);
  $("categoryBars").replaceChildren(...cats.map((c) => {
    const chg = c.change_pct;
    const fill = el("div", { class: "fill" + (c === weakest ? " hl" : "") }); fill.style.width = (c.share_pct / maxShare * 100) + "%";
    return el("div", { class: "catrow" },
      el("div", { class: "meta" }, el("b", { text: c.label }),
        el("span", {}, `${c.share_pct.toFixed(0)}% · ${c.sales_display} `,
          chg != null ? el("span", { class: "chg", text: `${chg >= 0 ? "▲" : "▼"} ${Math.abs(chg).toFixed(0)}%` }) : null)),
      el("div", { class: "track" }, fill));
  }));
  $("slowList").replaceChildren(el("b", { text: t("chart.slow") }), slow.map((p) => `${p.product} (${t("chart.sold", { n: p.units_sold })})`).join(", "));
}
function renderKhata(k, flashName) {
  $("simToggle").hidden = !authCfg.dev_mode;
  if (authCfg.dev_mode) $("simCustomer").replaceChildren(...(k.customers || []).map((c) => el("option", { value: c.customer_name, text: nameOf(c) })));
  $("khataSummary").textContent = k.customer_count ? t("khata.summary", { total: k.total_outstanding_display, n: k.customer_count }) : "";
  const rows = (k.customers || []).map((c) => el("tr", { class: c.customer_name === flashName ? "flash" : null, "data-customer": c.customer_name },
    el("td", {}, el("b", { text: nameOf(c) }), c.is_overdue ? el("span", { class: "badge", text: t("khata.overdue", { d: c.days_since_last_payment }) }) : null),
    el("td", { class: "num", text: c.outstanding_display }),
    el("td", { class: "muted", text: !c.last_payment_date ? t("khata.never") : c.days_since_last_payment === 0 ? t("khata.today") : t("khata.ago", { d: c.days_since_last_payment }) }),
    el("td", {}, el("button", { class: "btn small", type: "button", onclick: () => askFromDashboard(t("khata.remind_q", { first: first(nameOf(c)) })), text: t("khata.remind") }))));
  $("khataTable").tBodies[0].replaceChildren(...(rows.length ? rows : [el("tr", {}, el("td", { colspan: 4, class: "muted", text: t("khata.empty") }))]));
}

// ------------------------------------------------------------------ briefing (spoken)
function setBriefingBtn(speaking) {
  $("briefingBtn").classList.toggle("speaking", speaking);
  $("briefingBtn").querySelector(".ico").textContent = speaking ? "■" : "▶";
  $("briefingBtn").querySelector(".lbl").textContent = t(speaking ? "hero.stop" : "hero.listen");
}
$("briefingBtn").onclick = () => {
  if ($("briefingBtn").classList.contains("speaking")) { stopSpeaking(); return; }
  if (!dashboard) return;
  speak(dashboard.briefing, { force: true, onend: () => setBriefingBtn(false), briefing: true });
};

// ------------------------------------------------------------------ assistant
function renderSuggestions() {
  $("suggest").replaceChildren(...SUGGESTIONS[LANG].map((q) => el("button", { type: "button", onclick: () => ask(q), text: q })));
}
function welcome() {
  return el("div", { class: "welcome", id: "welcome" }, el("div", { class: "orb-big" }),
    el("p", {}, el("b", { text: t("asst.welcome") })), el("p", { class: "muted", text: t("asst.welcome2") }));
}
function addMsg(node) { $("welcome")?.remove(); $("chat").append(node); $("chat").scrollTop = $("chat").scrollHeight; return node; }
function replayButton(text) {
  return el("button", { class: "say", type: "button", onclick: (e) => speak(text, { force: true, button: e.currentTarget }), text: t("voice.replay") });
}
async function restoreConversation() {
  $("chat").replaceChildren();
  try {
    const r = await api("/api/conversation?session_id=" + encodeURIComponent(sessionId));
    if (!r.messages.length) $("chat").append(welcome());
    for (const m of r.messages) {
      addMsg(m.role === "user" ? el("div", { class: "msg me", text: m.text })
        : el("div", { class: "msg bot" }, el("div", { class: "reply", text: m.text }), el("div", { class: "msg-actions" }, replayButton(m.text))));
    }
  } catch (e) { if (e.message !== "Login required") $("chat").append(welcome()); }
}
function askFromDashboard(q) {
  if (window.matchMedia("(max-width: 1120px)").matches) $("assistant").scrollIntoView({ behavior: "smooth" });
  ask(q);
}
async function ask(text) {
  text = (text || "").trim();
  if (!text || busy || !merchant) return;
  busy = true; $("send").disabled = true;
  addMsg(el("div", { class: "msg me", text }));
  const typing = addMsg(el("div", { class: "msg bot typing", "aria-label": t("asst.thinking") }, el("span", { class: "typing-dots" }, el("span"), el("span"), el("span"))));
  try {
    const data = await postJSON("/api/ask", { text, session_id: sessionId, lang: LANG });
    typing.remove();
    const msg = addMsg(renderAnswer(data));
    speak(data.reply, { button: msg.querySelector(".say") });
    const changed = (data.tool_calls || []).find((tc) => ["sent", "saved"].includes(tc.output?.status));
    if (changed && changed.name === "link_upi_payment") { toast(t("toast.upi", { name: nameOf(changed.output) })); loadDashboard(changed.output.customer_name); }
    else if (changed) {
      toast(changed.output.status === "sent" ? t("toast.sent", { name: nameOf(changed.output) }) : t("toast.saved", { name: nameOf(changed.output), amt: changed.output.outstanding_after_display }));
      loadDashboard(changed.output.customer_name);
    }
  } catch (e) {
    typing.remove(); if (e.message !== "Login required") addMsg(el("div", { class: "err", text: "⚠ " + e.message }));
  } finally { busy = false; $("send").disabled = false; }
}
function renderAnswer(data) {
  const box = el("div", { class: "msg bot" + (data.error ? " err-reply" : "") }, el("div", { class: "reply", text: data.reply }));
  const lastByTool = new Map();
  for (const tc of data.tool_calls || []) lastByTool.set(tc.name, tc);
  for (const tc of lastByTool.values()) { const card = richCard(tc); if (card) box.append(card); }
  const actions = el("div", { class: "msg-actions" }, replayButton(data.reply));
  if ((data.tool_calls || []).length) {
    actions.append(el("details", { class: "how" }, el("summary", { text: t("asst.how", { n: data.tool_calls.length }) }),
      ...data.tool_calls.map((tc) => el("code", { text: `${tc.name}(${JSON.stringify(tc.input)})${tc.guard_note ? "  " + t("asst.guard") : ""}` }))));
  }
  box.append(actions);
  return box;
}
const stat = (l, v) => el("div", { class: "rc-stat" }, el("div", { class: "l", text: l }), el("div", { class: "v", text: v }));
const pill = (cls, text) => el("div", { class: "rc-status " + cls, text });
function richCard(tc) {
  const o = tc.output || {};
  if (o.error) return null;
  if (tc.name === "daily_summary" && o.total_sales_display) {
    const vs = o.pct_vs_same_weekday_avg;
    return el("div", { class: "rc", "data-card": "daily_summary" },
      el("div", { class: "rc-stats" }, stat(t("card.sales_of", { wd: dayName(o.weekday) }), o.total_sales_display), stat(t("card.payments"), String(o.transaction_count)),
        stat(t("card.vs", { wd: dayShort(o.weekday) }), vs == null ? "—" : `${vs >= 0 ? "▲" : "▼"} ${Math.abs(vs).toFixed(0)}%`)),
      miniWeekdays(o.weekday_pattern_avg_sales, o.weekday, o.weakest_weekday));
  }
  if (tc.name === "udhaar_lookup" && o.found) {
    return el("div", { class: "rc", "data-card": "udhaar_lookup" }, el("div", { class: "rc-title", text: o.customer_count ? t("card.khata_total", { total: o.total_outstanding_display }) : t("card.udhaar") }),
      ...(o.customers || []).slice(0, 6).map((c) => el("div", { class: "rc-row" },
        el("span", {}, el("b", { text: nameOf(c) }), c.is_overdue ? el("span", { class: "badge", text: t("card.since", { d: c.days_since_last_payment }) }) : null),
        el("span", { class: "v", text: c.outstanding_display }))));
  }
  if (tc.name === "lapsed_customers" && o.lapsed_count) {
    return el("div", { class: "rc", "data-card": "lapsed_customers" }, el("div", { class: "rc-title", text: t("card.risk", { amt: o.weekly_spend_at_risk_display }) }),
      ...o.lapsed_customers.map((c) => el("div", { class: "rc-row" },
        el("span", {}, el("b", { text: nameOf(c) }), el("div", { class: "s", text: t("card.lapsed_row", { d: c.days_since_last_visit, v: c.historical_visits_per_week }) })),
        el("span", { class: "v", text: fmt(c.weekly_spend_at_risk) }))));
  }
  if (tc.name === "product_performance") {
    const cats = o.categories_weakest_first || [];
    const max = Math.max(...cats.map((c) => c.share_pct), 1);
    const catName = (c) => (LANG === "en" ? c.category.charAt(0).toUpperCase() + c.category.slice(1) : c.category_hi || c.category);
    return el("div", { class: "rc", "data-card": "product_performance" }, el("div", { class: "rc-title", text: t("card.cat_title", { d: o.period_days }) }),
      ...cats.map((c, i) => { const f = el("div", { class: "fill" + (i === 0 ? " hl" : "") }); f.style.width = (c.share_pct / max * 100) + "%";
        return el("div", { class: "catrow" }, el("div", { class: "meta" }, el("b", { text: catName(c) }), el("span", { text: `${c.share_pct.toFixed(0)}%` })), el("div", { class: "track" }, f)); }),
      el("div", { class: "rc-legend" }, el("span", {}, el("i", { class: "sw-hl" }), t("card.cat_leg"))),
      el("div", { class: "slow" }, el("b", { text: t("card.slow") }), (o.slowest_products || []).slice(0, 3).map((p) => `${p.product} (${p.units_sold})`).join(", ")));
  }
  if (tc.name === "send_reminder" && o.reminder_message) {
    const st = o.status === "sent" ? ["ok", t(o.via === "telegram-demo" ? "card.rem_sent_demo" : "card.rem_sent")] : o.status === "confirmation_required" ? ["wait", t("card.rem_wait")] : ["bad", o.message || t("card.rem_fail")];
    return el("div", { class: "rc", "data-card": "send_reminder" }, el("div", { class: "rc-title", text: t("card.rem_title", { name: nameOf(o), amt: o.outstanding_display }) }),
      el("div", { class: "rc-msg", text: o.reminder_message }), pill(st[0], st[1]));
  }
  if (tc.name === "record_udhaar" && o.amount_display) {
    const st = o.status === "saved" ? ["ok", t("card.saved")] : o.status === "confirmation_required" ? ["wait", t("card.save_wait")] : ["bad", o.message || t("card.save_fail")];
    return el("div", { class: "rc", "data-card": "record_udhaar" }, el("div", { class: "rc-title", text: `${t(o.kind === "payment" ? "card.paid" : "card.credit")} · ${nameOf(o)}${o.new_customer ? t("card.newkhata") : ""}` }),
      el("div", { class: "rc-ledger" }, el("span", { text: o.amount_display }), el("span", { class: "arrow", text: "·" }),
        el("span", { class: "muted", text: fmt(o.outstanding_before) }), el("span", { class: "arrow", text: "→" }), el("span", { text: o.outstanding_after_display })),
      el("div", { class: "rc-legend" }, el("span", { text: t("card.ledger_leg") })), pill(st[0], st[1]));
  }
  if (tc.name === "pending_upi_payments" && o.count) {
    return el("div", { class: "rc", "data-card": "pending_upi_payments" }, el("div", { class: "rc-title", text: t("card.upi_title") }),
      ...o.payments.map((p) => el("div", { class: "rc-row" }, el("span", {}, el("b", { text: nameOf(p) }),
        el("div", { class: "s", text: t("card.upi_owes", { amt: p.outstanding_display }) })), el("span", { class: "v", text: p.amount_display }))));
  }
  if (tc.name === "link_upi_payment" && o.amount_display) {
    const st = o.status === "saved" ? ["ok", o.decision === "purchase" ? t("card.upi_done_purchase") : t("card.upi_done_repay", { after: o.outstanding_after_display })]
      : o.status === "confirmation_required" ? ["wait", t("card.save_wait")] : ["bad", o.message || t("card.save_fail")];
    return el("div", { class: "rc", "data-card": "link_upi_payment" },
      el("div", { class: "rc-title", text: t("card.upi_dec", { name: nameOf(o), amt: o.amount_display, d: t(o.decision === "purchase" ? "card.upi_purchase" : "card.upi_repay") }) }), pill(st[0], st[1]));
  }
  if (["send_reminder", "record_udhaar", "link_upi_payment"].includes(tc.name) && o.message) return el("div", { class: "rc" }, pill("bad", o.message));
  return null;
}
function miniWeekdays(pattern, selected, weakest) {
  const W = 320, H = 96, b = 18, top = 14, vals = WEEK.map((d) => pattern?.[d] || 0), max = Math.max(...vals, 1);
  const slot = W / 7, bw = Math.min(20, slot * 0.55);
  const svg = sv("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": t("card.week_title") });
  WEEK.forEach((d, i) => {
    const h = (H - b - top) * vals[i] / max, x = i * slot + (slot - bw) / 2;
    const cls = d === weakest ? " hl" : d === selected ? " today" : "";
    svg.append(sv("path", { class: "bar" + cls, d: barPath(x, H - b - h, bw, h) }));
    if (d === weakest || d === selected) svg.append(sv("text", { class: "dlabel", x: x + bw / 2, y: H - b - h - 4, "text-anchor": "middle", "font-size": 10 }, fmtK(vals[i])));
    svg.append(sv("text", { x: x + bw / 2, y: H - 4, "text-anchor": "middle", fill: d === selected ? "var(--ink)" : "var(--muted)", "font-size": 10.5, "font-weight": d === selected ? 700 : 400 }, dayShort(d)));
  });
  const legend = el("div", { class: "rc-legend" },
    selected !== weakest ? el("span", {}, el("i", { class: "sw-today" }), t("card.leg_sel", { wd: dayName(selected) })) : null,
    el("span", {}, el("i", { class: "sw-hl" }), t("card.leg_weak", { wd: dayName(weakest) })),
    el("span", {}, el("i", { class: "sw-bar" }), t("card.leg_rest")));
  return el("div", { class: "rc-chart" }, el("div", { class: "rc-title", text: t("card.week_title") }), svg, legend);
}
$("send").onclick = () => { ask($("text").value); $("text").value = ""; };
$("text").addEventListener("keydown", (e) => { if (e.key === "Enter") $("send").click(); });
// "New chat" starts a fresh conversation; the old one stays saved and can be reopened from "Past chats".
function setSession(id) { sessionId = id; store.set("munim.session." + merchant.id, id); }
$("resetBtn").onclick = () => {
  setSession("web-" + Math.random().toString(36).slice(2, 12));
  $("history").hidden = true;
  $("chat").replaceChildren(welcome());
};
function whenLabel(iso) {
  const d = new Date(iso);
  if (isNaN(d)) return "";
  const loc = LANG === "en" ? "en-IN" : LANG === "mr" ? "mr-IN" : "hi-IN";
  return d.toLocaleString(loc, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}
async function renderHistory() {
  let items = [];
  try { items = (await api("/api/conversations")).conversations; } catch (e) { toast("⚠ " + e.message); return; }
  $("history").replaceChildren(...(items.length ? items.map((c) => el("button", {
    type: "button", class: "history-item" + (c.session_id === sessionId ? " current" : ""), "data-session": c.session_id,
    onclick: async () => { setSession(c.session_id); $("history").hidden = true; await restoreConversation(); },
  }, el("b", { text: c.title }), el("span", { text: t("asst.history_meta", { when: whenLabel(c.last_at), n: c.questions })
    + (c.session_id === sessionId ? " · " + t("asst.history_now") : "") }))) : [el("p", { class: "muted", text: t("asst.history_empty") })]));
}
$("historyBtn").onclick = async () => {
  if (!$("history").hidden) { $("history").hidden = true; return; }
  await renderHistory();
  $("history").hidden = false;
};
$("voice").onchange = () => store.set("munim.voice", $("voice").value);

// ------------------------------------------------------------------ voice output
// Neural voice (server) through ONE reusable audio player, unlocked on the first tap/keypress so the
// browser's autoplay rule can't silently block answers. Browser voice only as a fallback, and ONLY
// with a voice of the right language: an English voice reading Hindi would skip the words and speak
// just the numbers.
const SR_LANG = { hi: "hi-IN", mr: "mr-IN", en: "en-IN" };
const player = new Audio();
let playerUrl = null, voices = [], audioUnlocked = false, speakToken = 0, novoiceShown = false;
window.__munimVoice = { last: null, voice: null };
function silentWavUrl() {
  const n = 800, buf = new ArrayBuffer(44 + n), v = new DataView(buf);
  const w = (o, s) => [...s].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
  w(0, "RIFF"); v.setUint32(4, 36 + n, true); w(8, "WAVE"); w(12, "fmt "); v.setUint32(16, 16, true); v.setUint16(20, 1, true);
  v.setUint16(22, 1, true); v.setUint32(24, 8000, true); v.setUint32(28, 8000, true); v.setUint16(32, 1, true); v.setUint16(34, 8, true);
  w(36, "data"); v.setUint32(40, n, true); for (let i = 0; i < n; i++) v.setUint8(44 + i, 128);
  return URL.createObjectURL(new Blob([buf], { type: "audio/wav" }));
}
function unlockAudio() {
  if (audioUnlocked) return;
  audioUnlocked = true;
  player.src = silentWavUrl();
  player.play().then(() => player.pause()).catch(() => { audioUnlocked = false; });
}
["pointerdown", "keydown", "touchstart"].forEach((ev) => document.addEventListener(ev, unlockAudio, { capture: true, passive: true }));
if ("speechSynthesis" in window) { voices = speechSynthesis.getVoices(); speechSynthesis.onvoiceschanged = () => { voices = speechSynthesis.getVoices(); }; }
function matchingBrowserVoice(lang) {
  const base = (SR_LANG[lang] || "hi-IN").slice(0, 2);
  const rank = (v) => (/natural|neural|online/i.test(v.name) ? 0 : /google/i.test(v.name) ? 1 : 2);
  return voices.filter((v) => v.lang.toLowerCase().startsWith(base)).sort((a, b) => rank(a) - rank(b))[0] || null;
}
function speechLang(text) {          // the voice must be able to read the script the text is written in
  const letters = [...(text || "")].filter((c) => /\p{L}/u.test(c));
  const dev = letters.filter((c) => c >= "\u0900" && c <= "\u097f").length;
  if (LANG === "en" && letters.length && dev > letters.length * 0.3) return "hi";
  return LANG;
}
function speakable(s, lang = LANG) {
  const rupees = lang === "en" ? "rupees" : "रुपये", pct = lang === "en" ? " percent" : " प्रतिशत";
  return s.replace(/₹\s?([\d,]+)/g, `$1 ${rupees}`).replace(/(\d)\s?%/g, `$1${pct}`).replace(/[—–]/g, ", ").replace(/[+−](?=₹|\d)/g, "");
}
function stopSpeaking() {
  speakToken++;
  try { player.pause(); } catch { /* ignore */ }
  if ("speechSynthesis" in window) speechSynthesis.cancel();
  setBriefingBtn(false);
}
function browserSpeak(text, onend) {
  const lang = speechLang(text);
  const v = ("speechSynthesis" in window) ? matchingBrowserVoice(lang) : null;
  if (!v) {                                             // never read Hindi with an English voice
    window.__munimVoice = { last: "novoice", voice: null };
    if (!novoiceShown) { toast(t("voice.novoice", { lang: LANG_NAME[lang][LANG] })); novoiceShown = true; }
    onend?.(); return;
  }
  const u = new SpeechSynthesisUtterance(speakable(text, lang));
  u.lang = v.lang; u.voice = v; u.rate = 0.95;
  u.onend = () => onend?.(); u.onerror = () => onend?.();
  window.__munimVoice = { last: "browser", voice: v.name };
  speechSynthesis.speak(u);
}
async function speak(text, { force = false, onend, button, briefing = false } = {}) {
  if (!text || (!force && !$("speak").checked)) { onend?.(); return; }
  stopSpeaking();
  const my = ++speakToken;
  if (briefing) setBriefingBtn(true);
  if (authCfg.voice_engine === "neural") {
    let blob = null;
    try {
      const r = await fetch("/api/tts", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, lang: LANG, gender: $("voice").value }) });
      if (r.ok) blob = await r.blob();
    } catch { /* network: fall through to browser voice */ }
    if (my !== speakToken) return;                       // a newer request replaced this one
    if (blob && blob.size > 0) {
      if (playerUrl) URL.revokeObjectURL(playerUrl);
      playerUrl = URL.createObjectURL(blob);
      player.src = playerUrl;
      player.onended = () => { if (my === speakToken) onend?.(); };
      try {
        await player.play();
        window.__munimVoice = { last: "neural", voice: $("voice").value };
        return;
      } catch (e) {
        if (e && e.name === "NotAllowedError") {           // autoplay blocked: the 🔊 button will play it on tap
          window.__munimVoice = { last: "blocked", voice: null };
          button?.classList.add("pulse");
          toast(t("voice.tap"));
          onend?.(); return;
        }
      }
    }
  }
  browserSpeak(text, onend);
}

// ------------------------------------------------------------------ speech-to-text (free, Chrome / Edge)
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
let rec = null, listening = false;
if (!SR) {
  $("mic").disabled = true;
  $("live").textContent = t("mic.none");
} else {
  $("mic").onclick = () => {
    if (listening) { rec.stop(); return; }
    stopSpeaking();
    rec = new SR(); rec.lang = SR_LANG[LANG] || "hi-IN"; rec.interimResults = true; rec.maxAlternatives = 1; rec.continuous = false;
    let finalText = "";
    rec.onstart = () => { listening = true; $("mic").classList.add("on"); $("live").textContent = t("mic.listening"); };
    rec.onresult = (e) => {
      let interim = "";
      for (const r of e.results) { if (r.isFinal) finalText = r[0].transcript; else interim += r[0].transcript; }
      $("live").textContent = finalText || interim;
    };
    rec.onerror = (e) => { $("live").textContent = e.error === "not-allowed" ? t("mic.denied") : t("mic.error", { e: e.error }); };
    rec.onend = () => {
      listening = false; $("mic").classList.remove("on");
      if (finalText) { $("live").textContent = ""; ask(finalText); }
      else if ($("live").textContent === t("mic.listening")) $("live").textContent = t("mic.silent");
    };
    rec.start();
  };
}

// ------------------------------------------------------------------ stale-tab detection
setInterval(async () => {
  try {
    const h = await fetch("/health", { cache: "no-store" }).then((r) => r.json());
    if (h.version && h.version !== APP_VERSION) banner("banner.refresh");
  } catch { /* server restarting */ }
}, 60000);

// ------------------------------------------------------------------ boot
(async () => {
  LANG = LANG_INDEX.hasOwnProperty(store.get("munim.lang")) ? store.get("munim.lang") : "hi";
  applyStatic();
  try {
    authCfg = await fetch("/api/auth/config").then((r) => (r.ok ? r.json() : Promise.reject(r.status)));
  } catch (status) {
    if (status === 404) banner("banner.outdated");
    authCfg = { method: "otp", dev_mode: false, voice_engine: "browser" };
  }
  if (authCfg.version && authCfg.version !== APP_VERSION) banner("banner.outdated");
  setLoginStep(authCfg.method === "pin" ? "pin" : "phone", { keepFocus: true });
  try {
    const sess = await fetch("/api/session", { credentials: "same-origin" }).then((r) => r.json());
    if (sess.authenticated) { await showApp(sess.merchant, sess.modes); return; }
    if (authCfg.dev_mode) {
      const d = await postJSON("/api/dev/login");
      const me = await api("/api/me");
      await showApp(d.merchant, me.modes);
      return;
    }
    showLogin();
  } catch (e) { showLogin(e.message || t("err.server")); }
})();
