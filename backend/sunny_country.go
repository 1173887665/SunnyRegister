package main

import (
	"fmt"
	"strings"
)

// Proxy countries use recognized ISO-style alpha-2 mappings. CC and MM are
// intentionally excluded to match the proxy import contract exposed by this app.
var sunnyProxyCountryCodes = func() map[string]bool {
	codes := strings.Fields(`
		AD AE AF AG AL AM AO AR AT AU AZ
		BA BB BD BE BF BG BH BI BJ BN BO BR BS BT BW BY BZ
		CA CD CF CG CH CI CL CM CN CO CR CU CV CY CZ
		DE DJ DK DM DO DZ EC EE EG ER ES ET FI FJ FM FR GA GB GD GE GH GM GN GQ GR GT GW GY
		HN HR HT HU ID IE IL IN IQ IR IS IT JM JO JP KE KG KH KI KM KN KP KR KW KZ
		LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MG MH MK ML MN MR MT MU MV MW MX MY MZ
		NA NE NG NI NL NO NP NR NZ OM PA PE PG PH PK PL PS PT PW PY QA RO RS RU RW
		SA SB SC SD SE SG SI SK SL SM SN SO SR SS ST SV SY SZ TD TG TH TJ TL TM TN TO TR TT TV TZ
		UA UG US UY UZ VA VC VE VN VU WS YE ZA ZM ZW
		AX AS AI AQ AW BL BM BQ BV CK CW CX EH FK FO GF GG GI GL GP GS GU HM HK IM IO JE KY
		MF MO MP MQ MS NC NF NU PF PM PN PR RE SH SJ SX TC TF TK TW UM VG VI WF YT
	`)
	result := make(map[string]bool, len(codes))
	for _, code := range codes {
		result[code] = true
	}
	return result
}()

func normalizeSunnyProxyCountry(value string) (string, error) {
	country := strings.TrimSpace(value)
	if len(country) != 2 || country[0] < 'A' || country[0] > 'Z' || country[1] < 'A' || country[1] > 'Z' {
		return "", fmt.Errorf("国家代码必须是两个大写英文字母，例如 JP、BR、US")
	}
	if !sunnyProxyCountryCodes[country] {
		return "", fmt.Errorf("国家代码 %s 不存在或不支持", country)
	}
	return country, nil
}
