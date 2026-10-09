"""Mock X client for MOCK_MODE. Generates realistic Telugu / English posts for every stream.

The generated "ground truth" tags are kept in MOCK_TRUTH so MockTagger can tag them
deterministically, and the whole UI works without any API keys.
"""
from __future__ import annotations

import itertools
import random
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from app import budget
from app.config import get_config, get_places

MOCK_TRUTH: Dict[str, dict] = {}
_seq = itertools.count()

# (issue, sentiment, urgency, telugu, english). {m} = mandal, {n} = number
LOCAL = [
    ("drinking_water", "neg", 3, "{m}లో {n} రోజులుగా తాగునీరు రావడం లేదు. అధికారులు పట్టించుకోవడం లేదు.",
     "No drinking water supply in {m} for {n} days. Officials are not responding."),
    ("drinking_water", "neg", 2, "{m} lo water tanker raledu, ఎండలో జనం అవస్థలు",
     "Water tanker did not come to {m}; people struggling in the heat."),
    ("irrigation", "neg", 2, "సాగర్ కాలువ నీరు {m} చివరి భూములకు చేరడం లేదు.",
     "Sagar canal water is not reaching tail-end fields in {m}."),
    ("farmers", "neg", 2, "{m}లో మిర్చి రైతులకు గిట్టుబాటు ధర లేదు, కొనుగోలు కేంద్రాలు తెరవాలి.",
     "Chilli farmers in {m} are not getting a fair price; open procurement centres."),
    ("roads", "neg", 1, "{m} రోడ్డు మొత్తం గుంతలమయం, ప్రయాణం నరకంగా మారింది.",
     "The road in {m} is full of potholes; travelling is a nightmare."),
    ("roads", "pos", 0, "{m}లో కొత్త సీసీ రోడ్డు పనులు ప్రారంభం. ధన్యవాదాలు.",
     "New CC road works started in {m}. Thank you."),
    ("power", "neg", 2, "{m}లో రాత్రంతా కరెంటు కోతలు, పిల్లలు చదువుకోలేకపోతున్నారు.",
     "Power cuts all night in {m}; children cannot study."),
    ("health", "neg", 3, "{m} ప్రభుత్వ ఆసుపత్రిలో డాక్టర్లు లేరు, రోగులు తిరిగి వెళ్తున్నారు.",
     "No doctors at the {m} government hospital; patients are being turned away."),
    ("law_and_order", "neg", 3, "{m}లో రాజకీయ ఘర్షణ, పోలీసులు వెంటనే స్పందించాలి.",
     "Political clash in {m}; police must act immediately."),
    ("welfare_schemes", "pos", 0, "{m}లో పింఛన్ల పంపిణీ సజావుగా జరిగింది.",
     "Pension distribution in {m} went smoothly today."),
    ("welfare_schemes", "neg", 1, "{m}లో రేషన్ బియ్యం ఈ నెల ఇంకా రాలేదు.",
     "Ration rice has not arrived in {m} this month."),
    ("education", "neu", 1, "{m} జూనియర్ కళాశాలలో అడ్మిషన్లు ప్రారంభమయ్యాయి.",
     "Admissions have opened at the {m} junior college."),
    ("jobs", "neg", 1, "{m} యువతకు ఉద్యోగాలు లేవు, పరిశ్రమలు రావాలి.",
     "No jobs for the youth of {m}; industries must come here."),
    ("other", "pos", 0, "{m} జాతర ఘనంగా జరిగింది.", "The {m} village festival was celebrated grandly."),
]
SPIKE_ISSUES = ["drinking_water", "health", "law_and_order", "power", "irrigation"]

# (handle, stance, issue, sentiment, telugu, english)
TDP = [
    ("JaiTDP", "pro_tdp", "welfare_schemes", "pos",
     "సూపర్ సిక్స్ హామీల అమలులో మరో ముందడుగు. పింఛన్లు ఇంటి వద్దకే.",
     "Another step in delivering the Super Six promises: pensions delivered at the doorstep."),
    ("JaiTDP", "anti_ysrcp", "corruption", "neg",
     "వైసీపీ ఐదేళ్ల పాలనలో ఇసుక దోపిడీ రాష్ట్రాన్ని నాశనం చేసింది.",
     "YSRCP's five years of sand-mining loot ruined the state."),
    ("ncbn", "pro_tdp", "jobs", "pos",
     "Amaravati works have restarted. Thousands of jobs for our youth.",
     "Amaravati works have restarted. Thousands of jobs for our youth."),
    ("naralokesh", "pro_tdp", "education", "pos",
     "Mega DSC: 16,000 teacher posts. Promise kept.", "Mega DSC: 16,000 teacher posts. Promise kept."),
    ("AndhraPradeshCM", "pro_tdp", "funds_budget", "pos",
     "CM met the Union Finance Minister in Delhi seeking Centre's support for Amaravati and Polavaram.",
     "CM met the Union Finance Minister in Delhi seeking Centre's support for Amaravati and Polavaram."),
    ("AndhraPradeshCM", "pro_tdp", "roads", "pos",
     "కేంద్ర నిధులతో పల్నాడులో జాతీయ రహదారి విస్తరణ పనులు వేగవంతం.",
     "With Central funds, national highway widening in Palnadu is speeding up."),
    ("AndhraPradeshCM", "pro_tdp", "welfare_schemes", "pos",
     "Annadata Sukhibhava funds credited to farmers' accounts today.",
     "Annadata Sukhibhava funds credited to farmers' accounts today."),
    ("SriKrishnaLavu", "pro_tdp", "irrigation", "pos",
     "Met the Union Jal Shakti Minister on Varikapudisela lift irrigation for Vinukonda and Macherla.",
     "Met the Union Jal Shakti Minister on Varikapudisela lift irrigation for Vinukonda and Macherla."),
    ("SriKrishnaLavu", "pro_tdp", "railways", "pos",
     "New train between Machilipatnam and Kurnool via Narasaraopet approved. Thanks to the Railway Minister.",
     "New train between Machilipatnam and Kurnool via Narasaraopet approved."),
    ("PullaRaoP_TDP", "pro_tdp", "roads", "pos",
     "చిలకలూరిపేటలో బైపాస్ రోడ్డు పనులను పరిశీలించాను.",
     "Inspected the bypass road works in Chilakaluripet."),
    ("Yarapathineni_S", "pro_tdp", "drinking_water", "pos",
     "గురజాల నియోజకవర్గంలో ఇంటింటికీ కుళాయి నీటి పనులు ప్రారంభం.",
     "Tap-water-to-every-home works started in Gurajala constituency."),
]
YSRCP = [
    ("YSRCParty", "anti_tdp", "farmers", "neg",
     "కూటమి ప్రభుత్వంలో మిర్చి రైతులు గిట్టుబాటు ధర లేక అల్లాడుతున్నారు.",
     "Under the coalition government, chilli farmers are suffering without fair prices."),
    ("YSRCParty", "anti_tdp", "law_and_order", "neg",
     "పల్నాడులో శాంతిభద్రతలు క్షీణించాయి. మా కార్యకర్తలపై దాడులు ఆగడం లేదు.",
     "Law and order has collapsed in Palnadu; attacks on our workers continue."),
    ("YSRCParty", "anti_tdp", "welfare_schemes", "neg",
     "Super Six promises remain on paper. Where is the unemployment allowance?",
     "Super Six promises remain on paper. Where is the unemployment allowance?"),
    ("ysjagan", "anti_tdp", "power", "neg",
     "Power charges hiked again. The coalition has broken its promise to the people.",
     "Power charges hiked again; the coalition broke its promise."),
    ("ysjagan", "pro_ysrcp", "welfare_schemes", "pos",
     "మన ప్రభుత్వంలో ప్రతి ఇంటికీ సంక్షేమం అందింది.",
     "In our government, welfare reached every household."),
    ("VidadalaRajini", "anti_tdp", "health", "neg",
     "Medical colleges we started are being handed to private players. Shameful.",
     "Medical colleges started under YSRCP are being privatised."),
]
MENTIONS = [
    ("education", "neu", 1, "@SriKrishnaLavu sir, Vinukonda needs a PG college. Students travel 60 km every day.",
     "Request for a PG college in Vinukonda; students travel 60 km daily."),
    ("railways", "pos", 0, "@SriKrishnaLavu thank you sir for the new train via Narasaraopet 🙏",
     "Thanks for the new train via Narasaraopet."),
    ("drinking_water", "neg", 2, "@SriKrishnaLavu గారు, దుర్గిలో తాగునీటి సమస్య తీవ్రంగా ఉంది. దయచేసి చూడండి.",
     "Severe drinking water problem in Durgi; asks the MP to intervene."),
    ("roads", "neg", 1, "@SriKrishnaLavu Piduguralla–Macherla road is in terrible shape. Accidents every week.",
     "Piduguralla–Macherla road is in terrible shape; weekly accidents."),
    ("other", "pos", 0, "@SriKrishnaLavu great speech in Lok Sabha today on AP's railway projects 👏",
     "Praise for the MP's Lok Sabha speech on AP railway projects."),
]
PARLIAMENT = [
    ("LokSabhaSectt", "parliament_business", "neu", "Lok Sabha adjourned till 2 PM amid protests.",
     "Lok Sabha adjourned till 2 PM amid protests."),
    ("LokSabhaSectt", "parliament_business", "neu", "List of Business for today: 3 Bills for consideration and passing.",
     "Today's List of Business: 3 Bills for consideration and passing."),
    ("sansad_tv", "parliament_business", "neu", "Watch live: Question Hour in Lok Sabha.",
     "Live: Question Hour in Lok Sabha."),
    ("ombirlakota", "parliament_business", "neu", "Chaired the meeting of the Business Advisory Committee.",
     "Speaker chaired the Business Advisory Committee meeting."),
    ("mpa_india", "parliament_business", "neu", "All-party meeting ahead of the session convened tomorrow.",
     "All-party meeting called ahead of the session."),
    ("RamMNK", "airports", "pos", "New UDAN routes connecting Vijayawada and Tirupati to 4 more cities.",
     "New UDAN routes connect Vijayawada and Tirupati to 4 more cities."),
    ("PemmasaniOnX", "central_schemes", "pos", "BharatNet: every gram panchayat in Guntur and Palnadu to get fibre by March.",
     "BharatNet fibre for every gram panchayat in Guntur and Palnadu by March."),
]
NATIONAL = [
    ("MORTHIndia", "highways", "pos", "₹{x} Cr sanctioned for 4-laning of an NH-16 stretch in Andhra Pradesh.",
     "₹{x} Cr sanctioned for 4-laning of an NH-16 stretch in Andhra Pradesh."),
    ("NHAI_Official", "highways", "pos", "Vijayawada bypass: 70% of work completed.",
     "Vijayawada bypass 70% complete."),
    ("RailMinIndia", "railways", "pos", "Doubling of the Guntur–Guntakal line in Andhra Pradesh to finish this year.",
     "Guntur–Guntakal line doubling in Andhra Pradesh to finish this year."),
    ("DoWRRDGR_MoJS", "polavaram", "neu", "Review meeting on Polavaram project progress held; ₹{x} Cr released.",
     "Polavaram progress reviewed; ₹{x} Cr released."),
    ("FinMinIndia", "funds_budget", "pos", "₹{x} Cr tax devolution released to Andhra Pradesh.",
     "₹{x} Cr tax devolution released to Andhra Pradesh."),
    ("MoRD_GoI", "central_schemes", "pos", "PMAY-G: 1.2 lakh houses sanctioned for Andhra Pradesh.",
     "PMAY-G: 1.2 lakh houses sanctioned for Andhra Pradesh."),
    ("AgriGoI", "agriculture", "pos", "PM-KISAN installment credited to 41 lakh farmers in Andhra Pradesh.",
     "PM-KISAN installment credited to 41 lakh AP farmers."),
    ("SteelMinIndia", "industry_investment", "neu", "Review of Visakhapatnam Steel Plant revival plan.",
     "Visakhapatnam Steel Plant revival plan reviewed."),
    ("narendramodi", "amaravati_capital", "pos", "Amaravati will become a symbol of a developed Andhra Pradesh.",
     "PM: Amaravati will become a symbol of a developed Andhra Pradesh."),
    ("PMOIndia", "industry_investment", "pos", "PM to lay foundation for projects worth ₹{x} Cr in Andhra Pradesh.",
     "PM to lay foundation for projects worth ₹{x} Cr in Andhra Pradesh."),
]
KEYWORD_POSTS = [
    ("polavaram", "neg", "Polavaram R&R families are still waiting for compensation.",
     "Polavaram displaced families still await compensation."),
    ("polavaram", "pos", "పోలవరం డయాఫ్రమ్ వాల్ పనులు వేగంగా జరుగుతున్నాయి.",
     "Polavaram diaphragm wall works are progressing fast."),
    ("amaravati_capital", "pos", "Amaravati funds from the Centre and World Bank will change the capital region.",
     "Central and World Bank funds for Amaravati will transform the capital region."),
    ("funds_budget", "neg", "Special status for Andhra is still a dream after 10 years.",
     "Special status for Andhra still unfulfilled after 10 years."),
    ("parliament_business", "neu", "Andhra Pradesh MPs raised the Polavaram issue in Lok Sabha today.",
     "AP MPs raised Polavaram in Lok Sabha."),
]
CITIZENS = ["ramesh_ap", "palnadu_voice", "narasaraopet_news", "telugu_farmer", "macherla_youth",
            "vinukonda_updates", "sattenapalle_now", "gurazala_talks", "guntur_citizen", "ap_observer"]
TRENDS_POOL = [
    "#ParliamentSession", "#Budget2026", "#AndhraPradesh", "#Amaravati", "#Polavaram", "#IPL",
    "#INDvsAUS", "#Monsoon", "#UPSC", "#Diwali", "#StockMarket", "#Sensex", "#ISRO", "#Gaganyaan",
    "#CricketTwitter", "#Bollywood", "#Delhi", "#AirPollution", "#DelhiMetro", "#NEET", "#JEE",
    "#PMKisan", "#SwachhBharat", "#DigitalIndia", "#MakeInIndia", "#Startup", "#AI", "#Tech",
    "#RupeeFall", "#GoldPrice", "#Election2026", "#BJP", "#Congress", "#TDP", "#YSRCP",
    "#Hyderabad", "#Vizag", "#Tirupati", "#Kisan", "#Railways",
]


def _new_id() -> str:
    return str(int(time.time() * 1000) * 1000 + next(_seq) % 1000)


def _metrics(rng: random.Random, scale: int) -> dict:
    likes = int(rng.paretovariate(1.4) * scale)
    return {
        "likes": likes,
        "retweets": int(likes * rng.uniform(0.1, 0.4)),
        "replies": int(likes * rng.uniform(0.05, 0.3)),
        "quotes": int(likes * rng.uniform(0.0, 0.1)),
        "impressions": likes * rng.randint(20, 60),
    }


def _post(rng, author, text, truth, scale=8, minutes=55) -> dict:
    pid = _new_id()
    MOCK_TRUTH[pid] = truth
    return {
        "id": pid,
        "author_id": str(abs(hash(author)) % 10**9),
        "author_username": author,
        "text": text,
        "lang": "te" if re.search(r"[ఀ-౿]", text) else "en",
        "created_at": (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=rng.randint(0, minutes))).replace(microsecond=0),
        "conversation_id": pid,
        **_metrics(rng, scale),
    }


def current_spike():
    """One (ac, mandal, issue) is 'blowing up' each hour, so Needs-attention has something to show."""
    places = get_places()
    mandals = [(ac.name, m.name) for ac in places.assembly_constituencies for m in places.ac(ac.name).mandals
               if not m.ambiguous]
    hour = int(time.time() // 3600)
    ac, mandal = mandals[(hour * 7) % len(mandals)]
    return ac, mandal, SPIKE_ISSUES[hour % len(SPIKE_ISSUES)]


class MockXClient:
    def __init__(self, seed: Optional[int] = None):
        self.rng = random.Random(seed)

    # ---- streams ----
    def _local(self, query: str) -> List[dict]:
        rng, places = self.rng, get_places()
        acs = [ac for ac in places.assembly_constituencies
               if any(a.lower() in query.lower() for a in ac.aliases + [m.aliases[0] for m in ac.mandals])]
        acs = acs or places.assembly_constituencies
        out = []
        for _ in range(rng.randint(1, 5)):
            ac = rng.choice(acs)
            mandal = rng.choice([m for m in ac.mandals if not m.ambiguous])
            issue, sent, urg, te, en = rng.choice(LOCAL)
            n = rng.randint(2, 9)
            use_te = rng.random() < 0.6
            text = (te if use_te else en).format(m=mandal.aliases[-1] if use_te else mandal.name, n=n)
            out.append(_post(rng, rng.choice(CITIZENS), text, {
                "sentiment": sent, "issue": issue, "party_stance": "neutral", "ac": ac.name,
                "mandal": mandal.name, "urgency": urg, "summary_en": en.format(m=mandal.name, n=n)}))
        spike_ac, spike_mandal, spike_issue = current_spike()
        if any(ac.name == spike_ac for ac in acs):
            mandal = places.ac(spike_ac)
            m = next(x for x in mandal.mandals if x.name == spike_mandal)
            templates = [t for t in LOCAL if t[0] == spike_issue and t[1] == "neg"]
            for _ in range(rng.randint(8, 12)):
                issue, sent, urg, te, en = rng.choice(templates)
                n = rng.randint(3, 7)
                use_te = rng.random() < 0.7
                text = (te if use_te else en).format(m=m.aliases[-1] if use_te else m.name, n=n)
                out.append(_post(rng, rng.choice(CITIZENS), text, {
                    "sentiment": "neg", "issue": issue, "party_stance": "neutral", "ac": spike_ac,
                    "mandal": spike_mandal, "urgency": urg, "summary_en": en.format(m=m.name, n=n)}, scale=15))
        return out

    def _party(self, query: str) -> List[dict]:
        rng = self.rng
        handles = {h.lower() for h in re.findall(r"(?<!-)from:(\w+)", query)}
        pool = [t for t in TDP + YSRCP if t[0].lower() in handles]
        out = []
        for _ in range(min(len(pool), rng.randint(1, 4))):
            handle, stance, issue, sent, te, en = rng.choice(pool)
            text = te if rng.random() < 0.5 else en
            out.append(_post(rng, handle, text, {
                "sentiment": sent, "issue": issue if issue in get_config().issues else "other",
                "party_stance": stance, "ac": None, "mandal": None, "urgency": 0, "summary_en": en},
                scale=200 if handle in ("ncbn", "ysjagan", "JaiTDP", "YSRCParty") else 40, minutes=120))
        return out

    def _mentions(self) -> List[dict]:
        rng = self.rng
        out = []
        for _ in range(rng.randint(1, 4)):
            issue, sent, urg, text, en = rng.choice(MENTIONS)
            out.append(_post(rng, rng.choice(CITIZENS), text, {
                "sentiment": sent, "issue": issue if issue in get_config().issues else "other",
                "party_stance": "neutral", "ac": None, "mandal": None, "urgency": urg, "summary_en": en}))
        return out

    def _delhi(self, query: str, pool, stream) -> List[dict]:
        rng = self.rng
        handles = {h.lower() for h in re.findall(r"(?<!-)from:(\w+)", query)}
        pool = [t for t in pool if t[0].lower() in handles] or pool
        out = []
        for _ in range(rng.randint(0, 3) if stream == "national" else rng.randint(1, 3)):
            handle, topic, sent, text, en = rng.choice(pool)
            x = rng.choice([180, 450, 1200, 2400, 3800])
            out.append(_post(rng, handle, text.format(x=x), {
                "sentiment": sent, "issue": topic, "party_stance": "neutral", "ac": None, "mandal": None,
                "urgency": 0, "summary_en": en.format(x=x), "ap_relevant": True}, scale=120, minutes=180))
        return out

    def _keywords(self) -> List[dict]:
        rng = self.rng
        out = []
        for _ in range(rng.randint(1, 3)):
            topic, sent, text, en = rng.choice(KEYWORD_POSTS)
            out.append(_post(rng, rng.choice(CITIZENS), text, {
                "sentiment": sent, "issue": topic, "party_stance": "neutral", "ac": None, "mandal": None,
                "urgency": 0, "summary_en": en, "ap_relevant": True}, minutes=180))
        return out

    def _explore(self, query: str) -> List[dict]:
        rng = self.rng
        words = re.sub(r"[()\"]|-?\w+:\S+|\bOR\b", " ", query).split()
        topic = " ".join(words[:3]) or "Palnadu"
        return [_post(rng, rng.choice(CITIZENS), f"Discussion about {topic} in Palnadu today. {i + 1}",
                      {"sentiment": rng.choice(["pos", "neu", "neg"]), "issue": "other", "party_stance": "neutral",
                       "ac": None, "mandal": None, "urgency": 0,
                       "summary_en": f"Post about {topic}"}) for i in range(rng.randint(3, 8))]

    # ---- XClient interface ----
    def search_recent(self, query: str, max_results: int = 20, since_id: Optional[str] = None,
                      pages: int = 1, stream: str = "explore") -> dict:
        if stream == "local":
            posts = self._local(query)
        elif stream == "party":
            posts = self._party(query)
        elif stream == "mp_mentions":
            posts = self._mentions()
        elif stream == "parliament":
            posts = self._delhi(query, PARLIAMENT, stream)
        elif stream == "national":
            posts = self._delhi(query, NATIONAL, stream)
        elif stream == "delhi_keywords":
            posts = self._keywords()
        else:
            posts = self._explore(query)
        posts = sorted(posts, key=lambda p: p["id"], reverse=True)[: max(10, min(100, max_results)) * pages]
        budget.record("x", "search_recent", len(posts), budget.x_cost(len(posts), len(posts)), stream)
        return {"posts": posts, "newest_id": posts[0]["id"] if posts else None}

    def tweets_by_ids(self, ids: List[str], stream: str = "explore") -> List[dict]:
        posts = [_post(self.rng, rng_name, f"Post {i} fetched by ID.", {
            "sentiment": "neu", "issue": "other", "party_stance": "neutral", "ac": None, "mandal": None,
            "urgency": 0, "summary_en": f"Post {i}"}) for i, rng_name in zip(ids, itertools.cycle(CITIZENS))]
        budget.record("x", "tweets", len(posts), budget.x_cost(len(posts), len(posts)), stream)
        return posts

    def trends_by_woeid(self, woeid: int, max_trends: int = 30) -> List[dict]:
        rng = random.Random(int(time.time() // 900) * 31 + woeid)
        names = rng.sample(TRENDS_POOL, 20)
        budget.record("x", "trends", 1, get_config().budget.cost_per_trends_call, "trends")
        return [{"name": n, "tweet_count": rng.choice([None, rng.randint(2_000, 400_000)])} for n in names]
