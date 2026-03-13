import os

import oracledb
from flask import Flask, jsonify, redirect, render_template, request, url_for


app = Flask(__name__)


def get_connection():
    user = os.environ.get("DB_USER")
    password = os.environ.get("DB_PASSWORD")
    dsn = os.environ.get("DB_DSN")

    if not user or not password or not dsn:
        raise ValueError(
            "Configure DB_USER, DB_PASSWORD e DB_DSN nas variaveis de ambiente."
        )

    return oracledb.connect(user=user, password=password, dsn=dsn)


def listar_pontos():
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT id_ponto, bairro, peso_kg, status
                FROM TB_PONTOS_COLETA
                ORDER BY bairro, id_ponto
                """
            )
            pontos = cursor.fetchall()

    total_pendente = sum(float(p[2]) for p in pontos if p[3] == 'PENDENTE')
    return pontos, total_pendente


def listar_bairros_pendentes():
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT DISTINCT bairro
                FROM TB_PONTOS_COLETA
                WHERE status = 'PENDENTE'
                ORDER BY id_ponto
                """
            )
            rows = cursor.fetchall()

    return [r[0] for r in rows]


def executar_processamento(bairro, capacidade_kg):
    bloco_plsql = """
    DECLARE
        v_bairro TB_PONTOS_COLETA.bairro%TYPE := :bairro;
        v_capacidade_kg TB_PONTOS_COLETA.peso_kg%TYPE := :capacidade_kg;
        v_peso_total TB_PONTOS_COLETA.peso_kg%TYPE := 0;

        CURSOR c_coletas IS
            SELECT id_ponto, peso_kg
            FROM TB_PONTOS_COLETA
            WHERE bairro = v_bairro
              AND status = 'PENDENTE'
            ORDER BY id_ponto
            FOR UPDATE;
    BEGIN
        FOR coleta IN c_coletas LOOP
            EXIT WHEN v_peso_total + coleta.peso_kg > v_capacidade_kg;

            UPDATE TB_PONTOS_COLETA
            SET status = 'COLETADO'
            WHERE CURRENT OF c_coletas;

            v_peso_total := v_peso_total + coleta.peso_kg;
        END LOOP;
    END;
    """

    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                bloco_plsql,
                {
                    "bairro": bairro,
                    "capacidade_kg": capacidade_kg,
                },
            )
        conn.commit()


def resetar_pontos_iniciais():
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM TB_PONTOS_COLETA")
            cursor.executemany(
                """
                INSERT INTO TB_PONTOS_COLETA (bairro, peso_kg)
                VALUES (:bairro, :peso_kg)
                """,
                [
                    {"bairro": "Centro", "peso_kg": 150},
                    {"bairro": "Centro", "peso_kg": 400},
                    {"bairro": "Vila Nova", "peso_kg": 200},
                    {"bairro": "Centro", "peso_kg": 1200},
                ],
            )
        conn.commit()


@app.route("/", methods=["GET", "POST"])
def home():
    """Simple dashboard with form + pending points list."""
    resultado_ok = request.args.get("ok")
    resultado_erro = request.args.get("erro")

    if request.method == "POST":
        try:
            bairro = request.form.get("bairro", "").strip()
            capacidade_kg = float(request.form.get("capacidade_kg", ""))

            if not bairro:
                raise ValueError("Selecione um bairro.")

            executar_processamento(bairro=bairro, capacidade_kg=capacidade_kg)
            resultado_ok = "Coleta processada com sucesso."
        except ValueError:
            resultado_erro = "Selecione um bairro e informe uma capacidade valida."
        except oracledb.Error as exc:
            resultado_erro = str(exc)

    try:
        pontos, total_pendente = listar_pontos()
        bairros = listar_bairros_pendentes()
        bairro_selecionado = request.form.get("bairro", "") if request.method == "POST" else ""
        return render_template(
            "index.html",
            pontos=pontos,
            bairros=bairros,
            bairro_selecionado=bairro_selecionado,
            total_pendente=int(total_pendente),
            resultado_ok=resultado_ok,
            resultado_erro=resultado_erro,
        )
    except (oracledb.Error, ValueError) as exc:
        return jsonify({"erro": str(exc)}), 500


@app.post("/resetar")
def resetar():
    try:
        resetar_pontos_iniciais()
        return redirect(url_for("home", ok="Dados reiniciados com os valores iniciais."))
    except (oracledb.Error, ValueError) as exc:
        return redirect(url_for("home", erro=str(exc)))


@app.post("/processar")
def processar():
    data = request.get_json(silent=True) or {}
    bairro = (data.get("bairro") or "").strip()
    capacidade_kg = data.get("capacidade_kg")

    if not bairro or capacidade_kg is None:
        return (
            jsonify(
                {
                    "erro": "Informe bairro e capacidade_kg no JSON.",
                    "exemplo": {
                        "bairro": "Centro",
                        "capacidade_kg": 1000,
                    },
                }
            ),
            400,
        )

    try:
        executar_processamento(bairro=bairro, capacidade_kg=capacidade_kg)

        return jsonify(
            {
                "mensagem": "Bloco PL/SQL executado com sucesso.",
                "bairro": bairro,
                "capacidade_kg": capacidade_kg,
            }
        )
    except (oracledb.Error, ValueError) as exc:
        return jsonify({"erro": str(exc)}), 500


if __name__ == "__main__":
    app.run(debug=True)
