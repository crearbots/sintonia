(function () {
  var barra = document.querySelectorAll("#barra .paso");
  function marca(n) {
    barra.forEach(function (b, i) {
      b.classList.toggle("on", i <= n);
    });
  }
  var form = document.getElementById("formPostular");
  if (!form) return;

  form.addEventListener("submit", function (ev) {
    if (document.getElementById("paso").value !== "guardar") ev.preventDefault();
  });

  var pasoCel = document.getElementById("pasoCel");
  var pasoNom = document.getElementById("pasoNom");
  var pasoSem = document.getElementById("pasoSem");
  var err = document.getElementById("errCel");

  function csrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.content : "";
  }

  function pintar() {
    document.querySelectorAll("#listaDias input[type=checkbox]").forEach(function (c) {
      var label = c.closest(".hueco");
      if (label) label.classList.toggle("marcado", c.checked);
    });
    document.querySelectorAll(".dia").forEach(function (dia) {
      var n = dia.querySelectorAll("input[type=checkbox]:checked").length;
      var cuenta = dia.querySelector(".cuenta");
      if (cuenta) cuenta.textContent = n ? n + (n === 1 ? " marcado" : " marcados") : "";
    });
  }

  function bloquear(activo) {
    var lista = document.getElementById("listaDias");
    if (!lista) return;
    lista.querySelectorAll('input[name="hueco"]').forEach(function (c) {
      if (activo) c.checked = false;
      c.disabled = activo;
    });
    lista.style.display = activo ? "none" : "";
    pintar();
  }

  function aplicarMios(mios, noPuede) {
    var set = mios || [];
    document.querySelectorAll("#listaDias input[type=checkbox]").forEach(function (c) {
      c.checked = set.indexOf(c.value) !== -1;
    });
    var no = document.getElementById("noPuedo");
    if (no) no.checked = !!noPuede;
    bloquear(!!noPuede);
    document.querySelectorAll(".dia").forEach(function (dia) {
      var n = dia.querySelectorAll("input[type=checkbox]:checked").length;
      if (n) dia.classList.add("abierto");
    });
    pintar();
  }

  function bindEnter(id, btnId) {
    var el = document.getElementById(id);
    if (!el) return;
    el.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter") {
        ev.preventDefault();
        document.getElementById(btnId).click();
      }
    });
  }
  bindEnter("celular", "btnCel");
  bindEnter("celular2", "btnCel");
  bindEnter("nombre", "btnNom");

  document.getElementById("btnCel").addEventListener("click", function () {
    var data = new FormData();
    data.append("celular", document.getElementById("celular").value);
    data.append("celular2", document.getElementById("celular2").value);
    var token = csrfToken();
    if (token) data.append("csrf_token", token);
    fetch(location.pathname + "/revisar", { method: "POST", body: data, credentials: "same-origin" })
      .then(function (r) {
        return r.json().then(function (d) {
          if (!r.ok) throw new Error(d.error || "HTTP " + r.status);
          return d;
        });
      })
      .then(function (res) {
        if (!res.ok) {
          err.textContent = res.error || "Revisa el celular";
          err.classList.remove("hidden");
          return;
        }
        err.classList.add("hidden");
        aplicarMios(res.mios, res.no_puede);
        pasoCel.classList.add("hidden");
        if (res.existe) {
          pasoSem.classList.remove("hidden");
          marca(1);
        } else {
          pasoNom.classList.remove("hidden");
          marca(1);
        }
      })
      .catch(function (e) {
        err.textContent = e.message || "No se pudo continuar";
        err.classList.remove("hidden");
      });
  });

  document.getElementById("btnNom").addEventListener("click", function () {
    if (!document.getElementById("nombre").value.trim()) {
      alert("Escribe tu nombre");
      return;
    }
    pasoNom.classList.add("hidden");
    pasoSem.classList.remove("hidden");
    marca(1);
  });

  document.getElementById("atrasNom").addEventListener("click", function () {
    pasoNom.classList.add("hidden");
    pasoCel.classList.remove("hidden");
    marca(0);
  });

  var revisar = document.getElementById("btnRevisar");
  var resumen = document.getElementById("pasoResumen");
  var listaResumen = document.getElementById("listaResumen");
  if (revisar) {
    revisar.addEventListener("click", function () {
      var noPuede = document.getElementById("noPuedo").checked;
      var marcados = Array.prototype.slice.call(
        document.querySelectorAll("#listaDias input[type=checkbox]:checked")
      );
      if (!noPuede && !marcados.length) {
        alert("Marca al menos un horario. Si no puedes esta semana, marca esa opción.");
        return;
      }
      listaResumen.innerHTML = "";
      if (noPuede) {
        listaResumen.innerHTML = "<li>Esta semana no puedes apoyar.</li>";
      } else {
        marcados.forEach(function (c) {
          var li = document.createElement("li");
          li.textContent = c.getAttribute("data-texto") || c.value;
          listaResumen.appendChild(li);
        });
      }
      pasoSem.classList.add("hidden");
      resumen.classList.remove("hidden");
      marca(2);
    });
  }

  document.getElementById("btnVolver").addEventListener("click", function () {
    resumen.classList.add("hidden");
    pasoSem.classList.remove("hidden");
    marca(1);
  });

  var guardar = document.getElementById("btnGuardar");
  if (guardar) {
    guardar.addEventListener("click", function () {
      document.getElementById("paso").value = "guardar";
      form.submit();
    });
  }

  var no = document.getElementById("noPuedo");
  if (no) {
    no.addEventListener("change", function () {
      bloquear(no.checked);
    });
    if (no.checked) bloquear(true);
  }

  document.querySelectorAll(".dia").forEach(function (dia) {
    function cuentaLocal() {
      var n = dia.querySelectorAll("input[type=checkbox]:checked").length;
      var cuenta = dia.querySelector(".cuenta");
      if (cuenta) cuenta.textContent = n ? n + (n === 1 ? " marcado" : " marcados") : "";
    }
    dia.querySelector(".abrir").addEventListener("click", function () {
      var abierto = dia.classList.contains("abierto");
      document.querySelectorAll(".dia").forEach(function (o) {
        o.classList.remove("abierto");
      });
      if (!abierto) dia.classList.add("abierto");
    });
    dia.querySelectorAll("input[type=checkbox]").forEach(function (c) {
      c.addEventListener("change", function () {
        var label = c.closest(".hueco");
        if (label) label.classList.toggle("marcado", c.checked);
        cuentaLocal();
      });
    });
    cuentaLocal();
  });
})();