"""Los tratamientos personales no deben arrastrar la oración que sigue al nombre."""
import pytest
from backend import engine, pipeline


CASES = [
    ('Sr. Oliva destaca que este proyecto representa una oportunidad', 'Oliva'),
    ('Sr. Felipe Muñoz consulta sobre el convenio con la universidad', 'Felipe Muñoz'),
    ('Sr. Andrés Oliva confirma que existe una carta de compromiso', 'Andrés Oliva'),
    ('Sr. Felipe Muñoz también pregunta por la fecha de cierre', 'Felipe Muñoz'),
    ('Sr. Andrés Oliva informa que se podría extender el plazo', 'Andrés Oliva'),
    ('Srta. Clara Soto, felicita a los expositores', 'Clara Soto'),
    ('Sr. Felipe Muñoz gestiona una nueva iniciativa', 'Felipe Muñoz'),
    ('Sr. Juan de la Cruz explica la propuesta', 'Juan de la Cruz'),
    ('Don Florian Mauricio Schneider Pérez, para estos efectos', 'Florian Mauricio Schneider Pérez'),
    ('Sr. Pedro Pérez de la comuna presenta antecedentes', 'Pedro Pérez'),
]


def page_for(text):
    words=[];x=10
    for token in text.split():
        width=max(len(token)*9,9)
        words.append([token,x,100,x+width,130]);x+=width+8
    return {'n':1,'width':2000,'height':2400,'words':words}


@pytest.mark.parametrize('text,expected', CASES)
def test_nombre_termina_antes_de_la_oracion_en_lectura_principal_y_alternativa(text, expected):
    page=page_for(text)
    for alternate in [False, True]:
        candidate={**page,'words':[],'verification_words':page['words']} if alternate else page
        fs=pipeline.detect_findings({'kind':'paged','format':'pdf','pages':[candidate]},['NOMBRE'])
        selected=[f for f in fs if f['entity_code']=='NOMBRE']
        assert len(selected)==1
        assert selected[0]['text']==expected
        expected_words=page['words'][1:1+len(expected.split())]
        assert selected[0]['boxes']==[w[1:] for w in expected_words]


@pytest.mark.parametrize('text,expected', CASES[:6])
@pytest.mark.parametrize('transform', [str.upper, str.title])
def test_prosa_en_mayusculas_no_se_convierte_en_apellido(text, expected, transform):
    text=transform(text);expected=transform(expected)
    fs=pipeline._detect_page_text_findings(page_for(text),page_for(text)['words'],['NOMBRE'])
    assert [f['text'] for f in fs]==[expected]
    spans=engine.detect(text,['NOMBRE'])
    assert [text[s.start:s.end] for s in spans]==[expected]


@pytest.mark.parametrize('text,expected', [
    ('Sr. josé gómez informa que se revisará', 'josé gómez'),
    ('Sr. J. Pérez explica los antecedentes', 'J Pérez'),
    ('Sr. Juan de la Cruz', 'Juan de la Cruz'),
    ('Sr. Carlos López y González presenta antecedentes', 'Carlos López y González'),
])
def test_tolerancia_ocr_iniciales_y_conectores_del_nombre(text,expected):
    fs=pipeline._title_context_names(page_for(text))
    assert [f['text'] for f in fs]==[expected]


def test_nombre_en_mitad_de_oracion_no_salta_a_sustantivo_en_linea_siguiente():
    words=[['Sr.',10,100,45,130],['Oliva',55,100,110,130],
           ['explica',120,100,190,130],['el',200,100,220,130],
           ['Centro',65,138,130,168]]
    page={'n':1,'width':1000,'height':2000,'words':words}
    findings=pipeline._detect_page_text_findings(page,words,['NOMBRE'])
    assert [f['text'] for f in findings]==['Oliva']
    assert findings[0]['boxes']==[words[1][1:]]


def test_cargo_de_concejala_no_se_agrega_como_apellido_envuelto():
    words=[['Clara',100,100,160,130],['Soto',170,100,230,130],
           ['Rojas',240,100,300,130],['CONCEJALA',125,140,280,170],
           ['PTO',290,140,340,170]]
    page={'n':1,'width':1000,'height':2000,'words':words}
    findings=pipeline._detect_page_text_findings(page,words,['NOMBRE'])
    assert [f['text'] for f in findings]==['Clara Soto Rojas']
    assert findings[0]['boxes']==[w[1:] for w in words[:3]]
