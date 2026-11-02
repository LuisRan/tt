"""Prueba de content script en Chromium con datos completamente sintéticos."""
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT = Path(__file__).resolve().parents[1]
HTML = '''<form id="registro">
<label for="nom">Nombre(s)</label><input id="nom" name="firstName" autocomplete="given-name">
<label for="ap">Apellido paterno</label><input id="ap" name="apellidoPaterno">
<label for="am">Apellido materno</label><input id="am" name="apellidoMaterno">
<label for="curp">CURP</label><input id="curp" name="curp">
<input id="mail" type="email" autocomplete="email">
<input id="dob" type="date" name="fechaNacimiento">
<label for="cp">Código postal</label><input id="cp" type="number">
<label for="dir">Domicilio</label><textarea id="dir"></textarea>
<label for="pais">Estado</label><select id="pais" name="estado"><option selected disabled value="">Seleccione</option><option value="CMX">Ciudad de México</option></select>
<label for="pass">Contraseña</label><input type="password" id="pass" value="">
<label for="otp">Código de verificación</label><input id="otp">
<label for="otro">Referencia bancaria</label><input id="otro">
<label for="ocupado">Nombre</label><input id="ocupado" value="No sobrescribir">
<button type="submit">Enviar</button></form><div id="nuevos"></div>'''
with sync_playwright() as pw:
    b=pw.chromium.launch(executable_path='/usr/bin/chromium', headless=True, args=['--no-sandbox'])
    page=b.new_page()
    page.set_content(HTML)
    page.evaluate('''() => {window.__envios = 0; document.querySelector('form').addEventListener('submit',e=>{e.preventDefault();window.__envios++;});
      window.__eventos=[]; document.getElementById('nom').addEventListener('input', e=>window.__eventos.push(e.type));
      window.chrome = {runtime:{id:'test-id',onMessage:{addListener(fn){window.__handler=fn;}}}};
    }''')
    page.add_script_tag(path=str(ROOT/'autofill-core.js'))
    page.add_script_tag(path=str(ROOT/'autofill.js'))
    result=page.evaluate('''() => { let r;window.__handler({tipo:'autofill:aplicar',sesion:'test-1',datos:{nombre:'Sofía',apellido_paterno:'Prueba',apellido_materno:'Simulada',curp:'PUSS010101MDFRMM01',correo:'test@example.invalid',fecha_nacimiento:'2001-01-01',codigo_postal:'01234',domicilio_completo:'Calle Ficticia 3',estado:'Ciudad de México'}},{id:'test-id'},x=>r=x);return r;}''')
    assert result['ok'],result
    assert result['completados']>=8,result
    data=page.evaluate('''() => Object.fromEntries(['nom','ap','am','curp','mail','dob','cp','dir','pais','pass','otp','otro','ocupado'].map(x=>[x,document.getElementById(x).value]))''')
    assert data['nom']=='Sofía', data
    assert data['ap']=='Prueba',data
    assert data['am']=='Simulada',data
    assert data['curp']=='PUSS010101MDFRMM01',data
    assert data['dob']=='2001-01-01',data
    assert data['cp']=='1234' or data['cp']=='01234',data # type=number representa números, el CP se lee como cadena en DOM en navegador
    assert data['dir']=='Calle Ficticia 3',data
    assert data['pais']=='CMX',data
    assert data['pass']==data['otp']==data['otro']=='',data
    assert data['ocupado']=='No sobrescribir',data
    assert page.evaluate('window.__envios')==0
    assert 'input' in page.evaluate('window.__eventos')
    page.evaluate('''() => {const l=document.createElement('label');l.htmlFor='dynamic';l.textContent='Correo electrónico';const x=document.createElement('input');x.id='dynamic';document.getElementById('nuevos').append(l,x)}''')
    page.wait_for_timeout(650)
    assert page.locator('#dynamic').input_value()=='test@example.invalid'
    print('DOM E2E: PASS; completados=',result['completados'], '; eventos y formulario dinámico: PASS')
    b.close()
