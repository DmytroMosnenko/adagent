import requests

payload = { 'api_key': '58ba5c22f199c99d08048d70522d2305', 'url': 'https://www.olx.pl/motoryzacja/samochody/lodzkie/?search%5Bfilter_float_price:to%5D=10000&search%5Bfilter_float_year:from%5D=2007&search%5Bfilter_enum_petrol%5D%5B0%5D=lpg&search%5Bfilter_enum_condition%5D%5B0%5D=notdamaged&search%5Bfilter_enum_transmission%5D%5B0%5D=automatic' }
r = requests.get('https://api.scraperapi.com/', params=payload)
print(r.text)

