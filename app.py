from flask import Flask, render_template, request, send_file, make_response
from datetime import datetime, date
import sqlite3, io, os, calendar
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm

app = Flask(__name__)
DB_PATH = os.path.join(os.path.dirname(__file__), 'aliquote.db')
MESI = ['Gennaio','Febbraio','Marzo','Aprile','Maggio','Giugno','Luglio','Agosto','Settembre','Ottobre','Novembre','Dicembre']
RATES = {
 'Basic':245.00,'Basic 5+':249.00,'Fixed Flight Pay':658.00,'Fixed Flight Pay 5+':683.00,
 'Ora Volo':4.69,'Ora Volo 5+':4.80,'Ora Volo >600':5.08,'Ora Volo >600 5+':5.38,
 'Bank Holiday':36.30,'1 Year Service Allowance':83.33,'Diaria':42.11,'Annual Leave':36.30,
 'Night Stop':45.00,'Landing 01:00-02:29':42.11,'Landing 02:30-03:59':63.11,
 'Training Day':35.00,'Sindacato':5.00,'VTO U/L Basic Quota':13.97,'VTO U/L FFP Quota':39.02,
 'N/S - Late Basic Quota':13.97,'N/S - Late FFP Quota':39.02,'Sickness':26.49
}
NUM_FIELDS = ['Ore Volo','Minuti','Diarie Volate','Diarie Non Volate','Annual Leave','Night Stop','Landing 01:00-02:29','Landing 02:30-03:59','Training Day','Sales Bonus','VTO prima del 15','VTO dopo il 15','Sickness','Sospensioni/N-S/Lates','Reddito CUD','Acconto Addizionale 2026','Rimborsi 730']

def db():
    c=sqlite3.connect(DB_PATH); c.row_factory=sqlite3.Row; return c

def num(form, key):
    raw=(form.get(key,'') or '0').strip().replace(',','.')
    try: v=float(raw)
    except ValueError: raise ValueError(f"Il campo '{key}' deve contenere un numero.")
    if v < 0: raise ValueError(f"Il campo '{key}' non può essere negativo.")
    return v

def calculate(f):
    crew=(f.get('CrewCode','').strip().upper()); basecode=f.get('Base','').strip().upper()
    comune=f.get('Comune','').strip().upper(); ass=f.get('Data Assunzione','').strip(); mese=f.get('Mese',MESI[datetime.now().month-1])
    if not crew or not basecode or not ass or not comune: raise ValueError('Compila CrewCode, Base, Data Assunzione e Comune di residenza.')
    try: data_ass=datetime.strptime(ass,'%d/%m/%Y').date()
    except ValueError: raise ValueError('Data assunzione non valida: usa GG/MM/AAAA (es. 01/01/2020).')
    if data_ass > date.today(): raise ValueError('La data di assunzione non può essere futura.')
    if not (f.get('Ore Volo','') or '').strip(): raise ValueError('Il campo Ore volo è obbligatorio: inserisci un numero, anche 0.')
    v={k:num(f,k) for k in NUM_FIELDS}
    if v['Minuti']>59: raise ValueError('I minuti di volo devono essere compresi tra 0 e 59.')
    if not (f.get('Diarie Volate','').strip() or f.get('Diarie Non Volate','').strip()): raise ValueError('Compila almeno uno dei due campi Diarie, anche con valore 0.')
    con=db()
    row=con.execute('SELECT regione FROM comuni WHERE UPPER(comune)=? LIMIT 1',(comune,)).fetchone()
    if not row: con.close(); raise ValueError('Comune non trovato nel database: seleziona un comune dall’elenco.')
    regione=row['regione']
    anni=round((date.today()-data_ass).days/365.25); senior=anni>=5
    basic=RATES['Basic 5+'] if senior else RATES['Basic']; ffp=RATES['Fixed Flight Pay 5+'] if senior else RATES['Fixed Flight Pay']
    high=f.get('high_hours')=='on'
    rate=RATES['Ora Volo >600 5+' if senior else 'Ora Volo >600'] if high else RATES['Ora Volo 5+' if senior else 'Ora Volo']
    ore=v['Ore Volo']; minuti=v['Minuti']; dv=int(v['Diarie Volate']); dnv=int(v['Diarie Non Volate'])
    cud=v['Reddito CUD']; acconto=v['Acconto Addizionale 2026']; rimborso=v['Rimborsi 730']
    if cud<=10000: col='fino10000'
    elif cud<=12000: col='fino12000'
    elif cud<=15000: col='fino15000'
    elif cud<=28000: col='fino28000'
    else: col='fino50000'
    r=con.execute(f'SELECT {col} AS aliquota FROM aliquote_comunali WHERE UPPER(comune)=? LIMIT 1',(comune,)).fetchone(); aliquota_comunale=(r['aliquota'] or 0) if r else 0
    colreg='sotto15000' if cud<15000 else 'da15000a28000'
    r=con.execute(f'SELECT {colreg} AS aliquota FROM aliquote_regionali WHERE UPPER(regione)=? ORDER BY anno DESC LIMIT 1',(regione,)).fetchone(); aliquota_regionale=(r['aliquota'] or 0) if r else 0
    con.close()
    training=int(v['Training Day'])*RATES['Training Day']; noshow=int(v['Sospensioni/N-S/Lates']); sickness_days=int(v['Sickness'])
    diaria_volata=dv*RATES['Diaria']; diaria_nonvolata=dnv*RATES['Diaria']; annual=int(v['Annual Leave'])*RATES['Annual Leave']; night=int(v['Night Stop'])*RATES['Night Stop']
    l30=int(v['Landing 01:00-02:29'])*RATES['Landing 01:00-02:29']; l60=int(v['Landing 02:30-03:59'])*RATES['Landing 02:30-03:59']; extra=v['Sales Bonus']
    vd=int(v['VTO prima del 15']); vdr=int(v['VTO dopo il 15']); service=RATES['1 Year Service Allowance']; totale_ffp=ffp+service
    vb=vd*RATES['VTO U/L Basic Quota']; vf=vd*RATES['VTO U/L FFP Quota']; nb=noshow*RATES['N/S - Late Basic Quota']; nf=noshow*RATES['N/S - Late FFP Quota']
    vbr=vdr*RATES['VTO U/L Basic Quota']; vfr=vdr*RATES['VTO U/L FFP Quota']
    recupero=max(0,sickness_days-3)*26.495; union=RATES['Sindacato'] if f.get('sindacato')=='on' else 0
    base_hours=ore+minuti/60; calc_hours=base_hours+dv*(35/60); flight1=calc_hours*rate
    flight=flight1+training+annual+RATES['Bank Holiday']
    imponibile_prev=(basic+diaria_nonvolata+extra+night+totale_ffp*.5+flight*.5-nb-vb-nf*.5-vf*.5)
    if imponibile_prev<1478.62: imponibile_prev=1478.62-nb-vb-vf*.5-nf*.5
    imponibile_prev=round(imponibile_prev)
    utile_tfr=basic+totale_ffp+diaria_nonvolata-vb-vf-nb-nf; tfr=utile_tfr*.0697; ctr=utile_tfr*.02 if f.get('ctr_vol')=='on' else 0
    ivs=imponibile_prev*.0919; fis=imponibile_prev*.0026667; cigs=imponibile_prev*.003
    base_l207=basic+diaria_nonvolata+extra+night+totale_ffp*.5+flight*.5-nb-vb-nf*.5-vf*.5-ivs-fis-cigs-ctr
    ann_l207=base_l207*12; aliquota_l207=.071 if ann_l207<8500 else (.0535 if ann_l207<=15000 else .048); ind_l207=base_l207*aliquota_l207
    mese_idx=MESI.index(mese)+1 if mese in MESI else datetime.now().month
    giorni_mese=calendar.monthrange(datetime.now().year,mese_idx)[1]; giorni_det=max(giorni_mese-vd-noshow,0)
    imponibile_irpef=basic+diaria_nonvolata+night+extra+totale_ffp*.5+flight*.5-nb-vb-nf*.5-vf*.5+rimborso+ind_l207-ctr-ivs-fis-cigs
    add_reg=(aliquota_regionale/100)*(cud/11 if cud>0 else imponibile_irpef)
    base_com=(aliquota_comunale*cud/100) if cud>0 else aliquota_comunale*(imponibile_irpef*12)/100
    add_com=(base_com-acconto)/11
    acconto_add=((aliquota_comunale*cud/100 if cud>0 else aliquota_comunale*(imponibile_irpef*12)/100)*.30)/9
    irpef_lorda=imponibile_irpef*.2299960092; imposta_lorda=irpef_lorda+add_com+add_reg+acconto_add
    competenze=basic+totale_ffp+flight+diaria_volata+diaria_nonvolata+l30+l60+night+extra+rimborso+recupero+ind_l207
    detrazioni=giorni_det*5.3561290323; imposta_netta=imposta_lorda-detrazioni
    trattenute=imposta_netta+ivs+fis+cigs+union+vb+nb+vf+nf+ctr; netto=competenze-trattenute
    items=[('Stipendio base',basic),('IVMG / Fixed Flight Pay',ffp),('Bank Holiday',RATES['Bank Holiday']),('Indennità aggiuntiva volo',service),('IVO / ore volo',flight1),('Sector Pay',flight),('Diarie volate (non tassate)',diaria_volata),('Diarie non volate (tassate)',diaria_nonvolata),('Annual Leave',annual),('Night Stop',night),('Landing 01:00–02:29',l30),('Landing 02:30–03:59',l60),('Training Day',training),('Sales Bonus',extra),('Rimborsi 730',rimborso),('Indennità malattia INPS',recupero),('Indennità L.207',ind_l207),('Imponibile previdenziale',imponibile_prev),('Contributo IVS',ivs),('Contributo FIS',fis),('Contributo CIGS',cigs),('Imponibile IRPEF',imponibile_irpef),('IRPEF lorda',irpef_lorda),('Addizionale regionale',add_reg),('Addizionale comunale',add_com),('Acconto addizionale comunale',acconto_add),('Detrazioni',detrazioni),('Imposta netta',imposta_netta),('Sindacato',union),('VTO Basic',vb),('VTO FFP',vf),('N/S - Late Basic',nb),('N/S - Late FFP',nf),('CTR volontario',ctr),('Retribuzione utile TFR',utile_tfr),('TFR fondo aereo (stima)',tfr)]
    # Organizzazione del riepilogo in sezioni
    sezioni = {
        'Competenze': [
            'Stipendio base',
            'IVMG / Fixed Flight Pay',
            'Bank Holiday',
            'Indennità aggiuntiva volo',
            'IVO / ore volo',
            'Sector Pay',
            'Diarie volate (non tassate)',
            'Diarie non volate (tassate)',
            'Annual Leave',
            'Night Stop',
            'Landing 01:00–02:29',
            'Landing 02:30–03:59',
            'Training Day',
            'Sales Bonus',
            'Rimborsi 730',
            'Indennità malattia INPS',
            'Indennità L.207',
        ],
        'Contributi previdenziali': [
            'Contributo IVS',
            'Contributo FIS',
            'Contributo CIGS',
        ],
        'Imposte e addizionali': [
            'Imponibile IRPEF',
            'IRPEF lorda',
            'Addizionale regionale',
            'Addizionale comunale',
            'Acconto addizionale comunale',
            'Imposta netta',
        ],
        'Detrazioni': [
            'Detrazioni',
        ],
        'Altre trattenute': [
            'Sindacato',
            'VTO Basic',
            'VTO FFP',
            'N/S - Late Basic',
            'N/S - Late FFP',
            'CTR volontario',
        ],
        'TFR': [
            'Retribuzione utile TFR',
            'TFR fondo aereo (stima)',
        ],
    }

    # Crea un dizionario con le voci e i relativi importi
    valori = dict(items)

    # Filtra le voci con importo diverso da zero
    riepilogo = {}

    for sezione, etichette in sezioni.items():
        voci = [
            (etichetta, valori[etichetta])
            for etichetta in etichette
            if etichetta in valori and abs(valori[etichetta]) > 1e-9
        ]

        if voci:
            riepilogo[sezione] = voci

    # Mantiene anche la lista originale per compatibilità
    items = [
        voce
        for voci in riepilogo.values()
        for voce in voci
    ]

    return {
        'crew': crew,
        'base': basecode,
        'comune': comune,
        'regione': regione,
        'mese': mese,
        'anni': anni,
        'netto': netto,
        'competenze': competenze,
        'trattenute': trattenute,
        'items': items,
        'riepilogo': riepilogo,
        'inputs': dict(f),
        'anno': datetime.now().year,
        'aliquota_comunale': aliquota_comunale,
        'aliquota_regionale': aliquota_regionale,
    }


@app.get('/')
def index():
    con=db(); comuni=[r[0] for r in con.execute('SELECT comune FROM comuni ORDER BY comune').fetchall()]; con.close()
    return render_template('index.html', comuni=comuni, mesi=MESI, mese_default=MESI[datetime.now().month-1], rates=RATES, result=None, error=None, form={})

@app.post('/calcola')
def calcola():
    con=db(); comuni=[r[0] for r in con.execute('SELECT comune FROM comuni ORDER BY comune').fetchall()]; con.close()
    form=request.form.to_dict()
    try:
        result=calculate(request.form)
        response=make_response(render_template('index.html',comuni=comuni,mesi=MESI,mese_default=MESI[datetime.now().month-1],rates=RATES,result=result,error=None,form=form))
        response.set_cookie('crewlink_last_result','',expires=0)
        return response
    except ValueError as e:
        return render_template('index.html',comuni=comuni,mesi=MESI,mese_default=MESI[datetime.now().month-1],rates=RATES,result=None,error=str(e),form=form),400


@app.post('/pdf')
def pdf():
    try:
        result = calculate(request.form)
    except ValueError as e:
        return str(e), 400

    buf = io.BytesIO()

    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        rightMargin=15*mm,
        leftMargin=15*mm,
        topMargin=15*mm,
        bottomMargin=15*mm
    )

    styles = getSampleStyleSheet()

    title = ParagraphStyle(
        'CrewLinkTitle',
        parent=styles['Title'],
        textColor=colors.HexColor('#17365d'),
        fontSize=19,
        leading=24,
        spaceAfter=8
    )

    subtitle = ParagraphStyle(
        'CrewLinkSubtitle',
        parent=styles['Normal'],
        fontSize=9,
        leading=14,
        textColor=colors.HexColor('#475569')
    )

    section_style = ParagraphStyle(
        'CrewLinkSection',
        parent=styles['Normal'],
        fontSize=10,
        leading=13,
        textColor=colors.white,
        fontName='Helvetica-Bold'
    )

    normal_style = ParagraphStyle(
        'CrewLinkNormal',
        parent=styles['Normal'],
        fontSize=8.5,
        leading=11
    )

    money_style = ParagraphStyle(
        'CrewLinkMoney',
        parent=normal_style,
        alignment=2
    )

    story = []

    # --------------------------------------------------
    # INTESTAZIONE
    # --------------------------------------------------

    story.append(
        Paragraph('CREWLINK — RIEPILOGO', title)
    )

    story.append(Spacer(1, 5))

    story.append(
        Paragraph(
            f"<b>CrewCode:</b> {result['crew']} "
            f"&nbsp;&nbsp;&nbsp; <b>Base:</b> {result['base']}",
            subtitle
        )
    )

    story.append(
        Paragraph(
            f"<b>Mese:</b> {result['mese']} {result['anno']} "
            f"&nbsp;&nbsp;&nbsp; "
            f"<b>Anni di servizio:</b> {result['anni']}",
            subtitle
        )
    )

    story.append(
        Paragraph(
            f"<b>Comune:</b> {result['comune']} "
            f"— <b>Regione:</b> {result['regione']}",
            subtitle
        )
    )
  
    # Ore di volo e totale diarie
    inputs = result['inputs']

    ore_volo = float(inputs.get('Ore Volo', 0) or 0)
    minuti_volo = int(float(inputs.get('Minuti', 0) or 0))

    diarie_volate = int(float(inputs.get('Diarie Volate', 0) or 0))
    diarie_non_volate = int(
        float(inputs.get('Diarie Non Volate', 0) or 0)
    )

    totale_diarie = diarie_volate + diarie_non_volate

    story.append(
        Paragraph(
            f"<b>Ore volate:</b> {ore_volo:g} h "
            f"{minuti_volo} min"
            f"&nbsp;&nbsp;&nbsp; "
            f"<b>Totale diarie:</b> {totale_diarie}",
            subtitle
        )
    )
  

    story.append(Spacer(1, 12))

    # --------------------------------------------------
    # DETTAGLIO DIVISO PER CATEGORIE
    # --------------------------------------------------

    categorie = result.get('riepilogo', {})

    colori = {
        'Competenze': colors.HexColor('#2457a7'),
        'Contributi': colors.HexColor('#475569'),
        'Imposte': colors.HexColor('#b45309'),
        'Detrazioni': colors.HexColor('#087f5b'),
        'Trattenute': colors.HexColor('#9f1239'),
        'TFR': colors.HexColor('#087f5b'),
    }

    for categoria, voci in categorie.items():

        # Intestazione della sezione
        LARGHEZZA_TABELLA = 165*mm

        intestazione = Table(
            [[Paragraph(categoria.upper(), section_style)]],
            colWidths=[LARGHEZZA_TABELLA],
            hAlign='LEFT'
        )

        intestazione.setStyle(
            TableStyle([
                (
                    'BACKGROUND',
                    (0, 0), (-1, -1),
                    colori.get(
                        categoria,
                        colors.HexColor('#17365d')
                    )
                ),
                ('LEFTPADDING', (0, 0), (-1, -1), 8),
                ('RIGHTPADDING', (0, 0), (-1, -1), 8),
                ('TOPPADDING', (0, 0), (-1, -1), 6),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ])
        )

        story.append(intestazione)
        story.append(Spacer(1, 4))

        # Voci della sezione
        dati = [
            [
                Paragraph('<b>Voce</b>', normal_style),
                Paragraph('<b>Importo (€)</b>', money_style)
            ]
        ]

        for label, value in voci:
            dati.append([
                Paragraph(str(label), normal_style),
                Paragraph(
                    f"{value:,.2f}".replace(',', 'X')
                    .replace('.', ',')
                    .replace('X', '.'),
                    money_style
                )
            ])

        tabella = Table(
            dati,
            colWidths=[120*mm, 45*mm],
            repeatRows=1,
            hAlign='LEFT'
        )

        tabella.setStyle(
            TableStyle([
                (
                    'BACKGROUND',
                    (0, 0), (-1, 0),
                    colors.HexColor('#edf3fb')
                ),
                (
                    'TEXTCOLOR',
                    (0, 0), (-1, 0),
                    colors.HexColor('#17365d')
                ),
                (
                    'GRID',
                    (0, 0), (-1, -1),
                    0.3,
                    colors.HexColor('#d8e0ea')
                ),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
                ('LEFTPADDING', (0, 0), (-1, -1), 7),
                ('RIGHTPADDING', (0, 0), (-1, -1), 7),
                ('TOPPADDING', (0, 0), (-1, -1), 5),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ])
        )

        story.append(tabella)
        story.append(Spacer(1, 10))

    # --------------------------------------------------
    # RIEPILOGO FINALE
    # --------------------------------------------------

    story.append(Spacer(1, 4))

    dati_finali = [
        [
            Paragraph('<b>TOTALE COMPETENZE</b>', normal_style),
            Paragraph(
                f"<b>{result['competenze']:,.2f}"
                .replace(',', 'X').replace('.', ',').replace('X', '.')
                + " €</b>",
                money_style
            )
        ],
        [
            Paragraph('<b>TOTALE TRATTENUTE</b>', normal_style),
            Paragraph(
                f"<b>{result['trattenute']:,.2f}"
                .replace(',', 'X').replace('.', ',').replace('X', '.')
                + " €</b>",
                money_style
            )
        ],
        [
            Paragraph('<b>NETTO STIMATO</b>', normal_style),
            Paragraph(
                f"<b>{result['netto']:,.2f}"
                .replace(',', 'X').replace('.', ',').replace('X', '.')
                + " €</b>",
                money_style
            )
        ]
    ]

    tabella_finale = Table(
        dati_finali,
        colWidths=[120*mm, 45*mm],
        hAlign='LEFT'
    )

    tabella_finale.setStyle(
        TableStyle([
            (
                'BACKGROUND',
                (0, 0), (-1, 1),
                colors.HexColor('#f1f5f9')
            ),
            (
                'BACKGROUND',
                (0, 2), (-1, 2),
                colors.HexColor('#dbeafe')
            ),
            (
                'TEXTCOLOR',
                (0, 2), (-1, 2),
                colors.HexColor('#17365d')
            ),
            ('GRID', (0, 0), (-1, -1), 0.5,
             colors.HexColor('#cbd5e1')),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
            ('LEFTPADDING', (0, 0), (-1, -1), 8),
            ('RIGHTPADDING', (0, 0), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ('FONTNAME', (0, 2), (-1, 2), 'Helvetica-Bold'),
        ])
    )

    story.append(tabella_finale)
    story.append(Spacer(1, 12))

    # --------------------------------------------------
    # NOTE FISCALI
    # --------------------------------------------------

    story.append(
        Paragraph(
            f"Aliquota comunale: {result['aliquota_comunale']}% "
            f"&nbsp;&nbsp;|&nbsp;&nbsp; "
            f"Aliquota regionale: {result['aliquota_regionale']}%",
            subtitle
        )
    )

    story.append(Spacer(1, 5))

    story.append(
        Paragraph(
            
            "",
            styles['Italic']
        )
    )

    # Generazione PDF
    doc.build(story)

    buf.seek(0)

    filename = (
        f"Payslip_CrewLink_{result['mese']}_{result['anno']}.pdf"
        .replace(' ', '_')
    )

    return send_file(
        buf,
        mimetype='application/pdf',
        as_attachment=True,
        download_name=filename
    )



if __name__ == "__main__":
    app.run(debug=True)
