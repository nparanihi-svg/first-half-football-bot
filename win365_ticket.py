"""Render a WIN365 ticket from immutable entry data and the current outcome."""
import io
import json

from PIL import Image, ImageDraw, ImageFont

PREFIX = 'ticket:v1:'
GOLD = '#ffdb42'
GREEN = '#48ed78'
WHITE = '#f7fbf8'


def ticket_body(snapshot, label, probability, fixture, quote):
    goals = fixture.get('goals') or {}
    score = 'Score unavailable'
    if all(type(goals.get(side)) is int for side in ('home', 'away')):
        score = f'{goals["home"]}–{goals["away"]}'
    corners = snapshot.half_corners
    corner_text = ('Corners unavailable' if corners is None
                   else f'{corners} {"corner" if corners == 1 else "corners"}')
    stats = f'{snapshot.total_shots:g} shots · {snapshot.shots_on_goal:g} on target'
    if snapshot.blocked_shots is not None and 'Corners' in label:
        stats += f' · {snapshot.blocked_shots:g} blocked'
    league = fixture.get('league') or {}
    return PREFIX + json.dumps({
        'market': label, 'teams': f'{snapshot.home} vs {snapshot.away}',
        'league': snapshot.league, 'country': league.get('country') or '',
        'entry': f'Selected {snapshot.elapsed}′ · {score} · {corner_text}',
        'stats': stats, 'chance': f'Model chance {probability:.1%}',
        'odds': f'Odds {quote[0]:.2f}' if quote else 'Odds unverified',
        'value': f'Line matched · Estimated value {probability * float(quote[0]) - 1:+.1%}' if quote else '',
    })


def render_ticket(body, record, *, status='LIVE WATCH', detail='Awaiting result'):
    ticket = json.loads(body.removeprefix(PREFIX))
    image = Image.new('RGB', (880, 920), '#101719')
    draw = ImageDraw.Draw(image)

    def font(size):
        try:
            return ImageFont.truetype('DejaVuSans-Bold.ttf', size)
        except OSError:
            return ImageFont.load_default(size=size)

    def center(text, y, size, color=WHITE, width=780):
        face = font(size)
        while draw.textlength(text, font=face) > width and size > 13:
            size -= 1
            face = font(size)
        if draw.textlength(text, font=face) > width:
            while text and draw.textlength(text + '…', font=face) > width:
                text = text[:-1]
            text += '…'
        draw.text((440, y), text, font=face, fill=color, anchor='mm')

    draw.rounded_rectangle((20, 20, 860, 900), 40, fill='#062519', outline=GOLD, width=7)
    for x in (20, 860):
        draw.ellipse((x - 27, 411, x + 27, 465), fill='#101719', outline=GOLD, width=4)
    draw.ellipse((85, 49, 195, 159), fill='#0e5a34', outline=WHITE, width=3)
    draw.rectangle((107, 67, 173, 141), outline=GREEN, width=2)
    draw.line((107, 104, 173, 104), fill=GREEN, width=2)
    draw.ellipse((124, 88, 156, 120), fill=WHITE)
    draw.regular_polygon((140, 104, 9), 5, fill='#062519')
    draw.text((220, 105), 'WIN', font=font(77), fill=WHITE, anchor='lm')
    draw.text((398, 105), '365', font=font(77), fill=GOLD, anchor='lm')
    color = {'WON': GREEN, 'LOST': '#ff6565'}.get(status, GOLD)
    mark = {'WON': '✓ ', 'LOST': '× '}.get(status, '● ')
    center(mark + status, 213, 56, color)
    draw.rounded_rectangle((54, 267, 826, 386), 22, fill=GOLD)
    center(ticket['market'], 308, 41, '#081a10', 735)
    center(ticket['odds'], 355, 36, '#081a10', 735)
    center(ticket['teams'], 430, 39, width=755)
    country = ticket['country']
    league = ticket['league'] or 'League unavailable'
    if country in {'Turkey', 'Türkiye'}:
        draw.rounded_rectangle((304, 465, 361, 505), 5, fill='#d91424')
        draw.ellipse((314, 474, 335, 495), fill=WHITE)
        draw.ellipse((320, 474, 338, 492), fill='#d91424')
        draw.text((339, 485), '★', font=font(14), fill=WHITE, anchor='mm')
        draw.text((376, 485), league[:32], font=font(31), fill=WHITE, anchor='lm')
    else:
        center(f'{country} · {league}' if country else league, 485, 31)
    center(ticket['entry'], 541, 32)
    center(ticket['stats'], 585, 30)
    center(ticket['chance'], 630, 32)
    center(ticket['value'], 673, 26)
    for y in (712, 817):
        draw.line((60, y, 820, y), fill=GREEN, width=3)
    center(detail, 765, 42, GOLD)
    center(record, 859, 29)
    output = io.BytesIO()
    image.save(output, format='PNG')
    return output.getvalue()
