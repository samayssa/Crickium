COUNTRY_FLAGS = {
    "india": "🇮🇳",
    "australia": "🇦🇺",
    "england": "🏴󠁧󠁢󠁥󠁮󠁧󠁿",
    "pakistan": "🇵🇰",
    "south africa": "🇿🇦",
    "new zealand": "🇳🇿",
    "sri lanka": "🇱🇰",
    "bangladesh": "🇧🇩",
    "afghanistan": "🇦🇫",
    "west indies": "🌴",
    "zimbabwe": "🇿🇼",
    "ireland": "🇮🇪",
    "scotland": "🏴󠁧󠁢󠁳󠁣󠁴󠁿",
    "netherlands": "🇳🇱",
    "usa": "🇺🇸",
    "united states": "🇺🇸",
    "canada": "🇨🇦",
    "nepal": "🇳🇵",
    "uae": "🇦🇪",
    "united arab emirates": "🇦🇪",
    "oman": "🇴🇲",
    "namibia": "🇳🇦",
    "papua new guinea": "🇵🇬",
}


def flag_for(country: str | None) -> str:
    if not country:
        return "🏳️"
    return COUNTRY_FLAGS.get(country.strip().lower(), "🏳️")
