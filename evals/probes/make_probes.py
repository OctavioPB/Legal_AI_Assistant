"""Builds evals/probes/tau_probes_v1.json: the two hand-made probe sets that calibrate the refusal
threshold tau (ADR-007). Covered topics carry gold cite_id fragments; absent topics are subjects
that none of the loaded sources address (or sibling jurisdictions the session must not reach).
Each topic has several wordings (formal, colloquial, English): scores swing wildly between
synonyms (Library-RAG), so one wording per topic would measure nothing.

authored_by is "ai-draft": a lawyer must review gold ids and absent topics before the numbers gate
a release (eval-runner skill).
"""

from __future__ import annotations

import json
from pathlib import Path


def covered(topic: str, session: str, gold: list[str], wordings: list[str]) -> dict[str, object]:
    return {"topic": topic, "session": session, "gold": gold, "wordings": wordings}


def absent(topic: str, session: str, wordings: list[str]) -> dict[str, object]:
    return {"topic": topic, "session": session, "wordings": wordings}


COVERED = [
    covered("rescisión sin responsabilidad del patrón", "fed", ["LFT-ART47"], [
        "causas de rescisión de la relación de trabajo sin responsabilidad para el patrón",
        "¿cuándo puede el patrón despedir a un trabajador sin pagarle indemnización?",
        "grounds for terminating an employee without liability for the employer"]),
    covered("indemnización por despido injustificado", "fed", ["LFT-ART48", "LFT-ART50"], [
        "indemnización de tres meses de salario por despido injustificado",
        "me despidieron sin causa, ¿qué me corresponde?",
        "severance pay after a wrongful dismissal in Mexico"]),
    covered("jornada máxima de trabajo", "fed", ["LFT-ART6"], [
        "duración máxima de la jornada diurna de trabajo",
        "¿cuántas horas puedo trabajar al día?",
        "maximum daily working hours"]),
    covered("vacaciones anuales", "fed", ["LFT-ART76"], [
        "días de vacaciones que corresponden al trabajador por año de servicio",
        "¿cuántos días de vacaciones me tocan al cumplir un año?",
        "annual paid vacation days for employees"]),
    covered("aguinaldo", "fed", ["LFT-ART87"], [
        "el aguinaldo anual equivalente a quince días de salario",
        "¿cuándo se paga el aguinaldo y de cuánto es?",
        "year-end bonus owed to workers"]),
    covered("huelga", "fed", ["LFT-ART44"], [
        "definición de huelga suspensión temporal del trabajo",
        "¿qué es una huelga y quién puede declararla?",
        "right to strike and its legal definition"]),
    covered("trabajo de menores", "fed", ["LFT-ART22", "LFT-ART17"], [
        "prohibición de utilizar el trabajo de menores de quince años",
        "¿a qué edad puede trabajar un adolescente?",
        "minimum age for employment"]),
    covered("libertad de expresión", "fed", ["CPEUM-ART6", "CPEUM-ART7"], [
        "la manifestación de las ideas no será objeto de inquisición judicial",
        "¿la Constitución protege la libertad de expresión?",
        "constitutional protection of freedom of speech"]),
    covered("principio pro persona y derechos humanos", "fed", ["CPEUM-ART1"], [
        "todas las personas gozarán de los derechos humanos reconocidos en la Constitución",
        "¿qué es el principio pro persona?",
        "human rights recognized in the Mexican Constitution"]),
    covered("prisión preventiva", "fed", ["CPEUM-ART19", "CPEUM-ART18"], [
        "prisión preventiva oficiosa por delitos graves",
        "¿cuándo puede un juez ordenar prisión preventiva?",
        "pretrial detention requirements"]),
    covered("derechos del imputado", "fed", ["CPEUM-ART20"], [
        "derechos de toda persona imputada presunción de inocencia",
        "¿qué derechos tengo si me acusan de un delito?",
        "rights of the accused and presumption of innocence"]),
    covered("expropiación", "fed", ["CPEUM-ART27"], [
        "expropiación por causa de utilidad pública y mediante indemnización",
        "¿puede el Estado quitarme mi terreno?",
        "expropriation for public use with compensation"]),
    covered("prescripción adquisitiva", "fed", ["CCF-ART1", "CCF-ART11"], [
        "prescripción positiva de bienes inmuebles por posesión",
        "¿en cuánto tiempo puedo adquirir un inmueble por posesión?",
        "adverse possession of real property"]),
    covered("compraventa", "fed", ["CCF-ART22"], [
        "el contrato de compraventa obliga a transferir la propiedad de una cosa por un precio cierto",
        "¿qué es un contrato de compraventa?",
        "sales contract elements price and object"]),
    covered("arrendamiento", "fed", ["CCF-ART23"], [
        "contrato de arrendamiento obligaciones del arrendador y arrendatario",
        "¿qué obligaciones tiene el inquilino?",
        "lease agreement obligations of landlord and tenant"]),
    covered("homicidio", "fed", ["CPF-ART30", "CPF-ART31", "CPF-ART32"], [
        "homicidio calificado penalidad prisión",
        "¿cuántos años de cárcel tiene el homicidio?",
        "penalty for aggravated homicide"]),
    covered("robo", "fed", ["CPF-ART36", "CPF-ART37"], [
        "delito de robo se impondrá pena según el valor de lo robado",
        "¿qué castigo tiene el robo?",
        "theft penalties depending on value"]),
    covered("legítima defensa", "fed", ["CPF-ART15"], [
        "causas de exclusión del delito legítima defensa",
        "¿cuándo no hay delito por defenderse?",
        "self-defense as an exclusion of criminal liability"]),
    covered("fraude", "fed", ["CPF-ART38"], [
        "delito de fraude engaño o aprovechamiento del error",
        "¿qué pena tiene estafar a alguien?",
        "fraud offense definition and penalty"]),
    covered("prescripción adquisitiva en Nuevo León", "nl", ["CCNL"], [
        "prescripción adquisitiva de buena fe de bienes inmuebles",
        "¿cómo se adquiere un inmueble por posesión en Nuevo León?",
        "how to acquire real property by possession in Nuevo León"]),
    covered("matrimonio en Nuevo León", "nl", ["CCNL"], [
        "requisitos para contraer matrimonio",
        "¿qué se necesita para casarse?",
        "requirements to get married"]),
    covered("justicia cívica Monterrey", "mty", ["RJCMTY"], [
        "faltas administrativas y sanciones del reglamento de justicia cívica",
        "¿qué pasa si cometo una falta administrativa en Monterrey?",
        "civic justice administrative offenses in Monterrey"]),
    covered("administración pública municipal", "mty", ["RAPMTY"], [
        "atribuciones de las secretarías de la administración pública municipal",
        "¿qué hace la Secretaría de Seguridad municipal?",
        "powers of the municipal government departments"]),
    covered("whistleblower retaliation California", "ca", ["LAB-SEC1102P5"], [
        "employer retaliation against an employee who discloses information to the government",
        "can my boss fire me for reporting a violation?",
        "represalias del patrón contra un trabajador que denuncia"]),
    covered("meal and rest periods California", "ca", ["LAB-SEC226P7", "LAB-SEC512"], [
        "meal period and rest break premium pay",
        "does an employer have to give me a lunch break?",
        "descansos y comidas obligatorias en California"]),
    covered("overtime California", "ca", ["LAB-SEC510"], [
        "overtime compensation for work over eight hours in a day",
        "how much overtime pay am I owed?",
        "pago de horas extra en California"]),
    covered("minimum wage California", "ca", ["LAB-SEC1182"], [
        "minimum wage rate for employees",
        "what is the legal minimum wage?",
        "salario mínimo en California"]),
]  # fmt: skip

ABSENT = [
    absent("impuesto sobre la renta", "fed", [
        "tasa del impuesto sobre la renta de personas morales",
        "¿cuánto ISR debo pagar este año?", "corporate income tax rate"]),
    absent("patentes y marcas", "fed", [
        "requisitos para registrar una patente",
        "¿cómo registro una marca ante el IMPI?", "how to file a trademark application"]),
    absent("derecho aeronáutico", "fed", [
        "responsabilidad de las aerolíneas por equipaje perdido",
        "¿qué derechos tengo si cancelan mi vuelo?", "airline liability for delayed flights"]),
    absent("concurso mercantil", "fed", [
        "procedimiento de concurso mercantil y quiebra de una empresa",
        "¿cómo declaro la quiebra de mi negocio?", "corporate bankruptcy proceedings"]),
    absent("migración y visas", "fed", [
        "requisitos para obtener una visa de residente temporal",
        "¿cómo tramito la residencia permanente?", "immigration visa requirements"]),
    absent("derecho ambiental", "fed", [
        "manifestación de impacto ambiental para una obra",
        "¿necesito permiso ambiental para construir?", "environmental impact assessment"]),
    absent("juicio de amparo", "fed", [
        "plazo para promover el juicio de amparo indirecto",
        "¿cómo presento un amparo?", "deadline to file an amparo lawsuit"]),
    absent("seguridad social IMSS", "fed", [
        "cuotas obrero patronales del IMSS",
        "¿cuánto se paga al seguro social por empleado?", "social security contributions"]),
    absent("protección de datos personales", "fed", [
        "aviso de privacidad y derechos ARCO",
        "¿cómo ejerzo mis derechos de acceso a mis datos personales?",
        "personal data protection rights"]),
    absent("hidrocarburos", "fed", [
        "permisos para exploración petrolera en aguas profundas",
        "¿quién regula la extracción de hidrocarburos?", "offshore oil exploration permits"]),
    absent("receta de cocina", "fed", [
        "receta de pastel de chocolate con tres huevos",
        "¿cómo preparo unas enchiladas verdes?", "how to bake a chocolate cake"]),
    absent("deportes", "fed", [
        "resultado del partido de fútbol de anoche",
        "¿quién ganó el campeonato de la liga?", "who won the football championship"]),
    absent("programación", "fed", [
        "cómo ordenar una lista en Python",
        "escribe una función que invierta una cadena", "how to sort a list in Python"]),
    absent("medicina", "fed", [
        "tratamiento para el dolor de cabeza y la migraña",
        "¿qué medicina tomo para la gripa?", "treatment for a persistent headache"]),
    absent("clima y viajes", "fed", [
        "pronóstico del clima para mañana en Guadalajara",
        "¿qué hotel me recomiendas en Cancún?", "best hotels in Cancun"]),
    absent("mercados financieros", "fed", [
        "precio de las acciones de Apple hoy",
        "¿conviene comprar bitcoin ahora?", "should I buy tech stocks this week"]),
    absent("municipio hermano: San Pedro", "mty", [
        "permiso de construcción en San Pedro Garza García",
        "reglamento de zonificación de San Pedro Garza García",
        "building permit in San Pedro Garza García"]),
    absent("estado hermano: Jalisco", "nl", [
        "código civil de Jalisco sobre la posesión",
        "¿qué dice la ley de Jalisco sobre herencias?", "Jalisco inheritance rules"]),
    absent("California Vehicle Code", "ca", [
        "fines for speeding on a California highway",
        "California vehicle registration deadline",
        "multas por exceso de velocidad en California"]),
    absent("California Family Code", "ca", [
        "child custody after divorce in California",
        "community property division in a California divorce",
        "custodia de hijos en un divorcio en California"]),
    absent("derecho mexicano en sesión de California", "ca", [
        "causas de rescisión de la relación de trabajo sin responsabilidad para el patrón",
        "Ley Federal del Trabajo aguinaldo", "artículo 47 de la Ley Federal del Trabajo"]),
    absent("GDPR", "ca", [
        "GDPR data transfer to third countries",
        "right to be forgotten under European law",
        "derecho al olvido en la Unión Europea"]),
]  # fmt: skip

if __name__ == "__main__":
    doc = {
        "version": 1,
        "authored_by": "ai-draft",
        "reviewed_by": None,
        "note": "Probe sets for tau calibration (ADR-007). A lawyer must review gold ids and "
        "absent topics before these numbers gate a release.",
        "covered": COVERED,
        "absent": ABSENT,
    }
    out = Path(__file__).with_name("tau_probes_v1.json")
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    n = lambda xs: sum(len(x["wordings"]) for x in xs)  # type: ignore[arg-type]  # noqa: E731
    print(
        f"{len(COVERED)} covered topics ({n(COVERED)} wordings), {len(ABSENT)} absent ({n(ABSENT)})"
    )
