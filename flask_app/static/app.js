const $ = (id) => document.getElementById(id);

const video = $("video");
const overlay = $("overlay");
const capture = document.createElement("canvas");

let token = null;
let stream = null;
let running = false;
let busy = false;
let previewUrl = null;

async function post(url, form) {
    const response = await fetch(url, {
        method: "POST",
        body: form,
    });
    const data = await response.json();

    if (!response.ok) {
        throw new Error(data.error || "Erreur du serveur.");
    }
    return data;
}

function display(data) {
    $("result").dataset.status = data.status;

    const titles = {
        granted: "ACCESS GRANTED",
        denied: "ACCESS DENIED",
        waiting: "Processing...",
        idle: "Ready to analyse",
    };

    $("title").textContent = titles[data.status];
    $("status-icon").textContent =
        data.status === "granted" ? "✓" :
            data.status === "denied" ? "×" : "◎";

    $("score").textContent =
        data.score == null
            ? "—"
            : `${(data.score * 100).toFixed(1)}%`;

    $("message").textContent = data.message;
    $("count").textContent = `${data.count || 0} / 5`;
    $("latency").textContent =
        data.latency_ms == null ? "—" : `${data.latency_ms} ms`;
}

function clearOverlay() {
    overlay.getContext("2d").clearRect(
        0, 0, overlay.width, overlay.height
    );
}

function stopCamera() {
    running = false;
    if (stream) stream.getTracks().forEach(track => track.stop());

    stream = null;
    video.srcObject = null;
    clearOverlay();

    $("placeholder").hidden = false;
    $("start").disabled = !token;
    $("stop").disabled = true;
    $("upload").disabled = false;

    display({
        status: "idle",
        message: "Turn on the camera to scan.",
    });
}

$("upload").addEventListener("change", async (event) => {
    const file = event.target.files[0];
    if (!file) return;

    stopCamera();
    $("start").disabled = true;
    $("notice").textContent = "Analyse de la référence…";

    const form = new FormData();
    form.append("image", file);
    if (token) form.append("token", token);

    // L'ancienne référence ne doit pas servir si l'import échoue.
    token = null;
    // $("reference-status").textContent = "Référence en cours de validation";

    try {
        const data = await post("/api/reference", form);
        token = data.token;

        if (previewUrl) URL.revokeObjectURL(previewUrl);
        previewUrl = URL.createObjectURL(file);

        $("preview").src = previewUrl;
        $("preview").hidden = false;
        // $("reference-status").textContent = "Référence prête";
        $("notice").textContent = "Référence validée. Démarrez le scan.";
        $("start").disabled = false;
    } catch (error) {
        $("preview").hidden = true;
        $("reference-status").textContent = "Référence non validée";
        $("notice").textContent = error.message;
    }
});

$("start").addEventListener("click", async () => {
    if (!token || running || busy) return;

    $("start").disabled = true;

    try {
        const form = new FormData();
        form.append("token", token);
        await post("/api/reset", form);

        stream = await navigator.mediaDevices.getUserMedia({
            video: {
                width: { ideal: 640 },
                height: { ideal: 480 },
                aspectRatio: { ideal: 4 / 3 },
            },
            audio: false,
        });

        video.srcObject = stream;
        await video.play();

        // Ajuster le conteneur à la vidéo pour aligner le cadre.
        video.parentElement.style.aspectRatio =
            `${video.videoWidth} / ${video.videoHeight}`;

        running = true;
        $("placeholder").hidden = true;
        $("stop").disabled = false;
        $("upload").disabled = true;
        $("notice").textContent = "Scan active — look at the camera.";

        scan();
    } catch (error) {
        stopCamera();
        $("notice").textContent = `Démarrage impossible : ${error.message}`;
    }
});

$("stop").addEventListener("click", stopCamera);

async function scan() {
    if (!running) return;

    busy = true;

    try {
        const ratio = Math.min(1, 640 / video.videoWidth);
        capture.width = Math.round(video.videoWidth * ratio);
        capture.height = Math.round(video.videoHeight * ratio);

        capture.getContext("2d").drawImage(
            video, 0, 0, capture.width, capture.height
        );

        const blob = await new Promise(resolve =>
            capture.toBlob(resolve, "image/jpeg", 0.85)
        );
        if (!blob) throw new Error("Capture impossible.");

        const form = new FormData();
        form.append("image", blob, "frame.jpg");
        form.append("token", token);

        const data = await post("/api/analyze", form);
        if (!running) return;

        display(data);

        overlay.width = capture.width;
        overlay.height = capture.height;
        const context = overlay.getContext("2d");

        if (data.box) {
            const [x, y, w, h] = data.box;
            context.strokeStyle =
                data.status === "granted" ? "#4cdda6" :
                    data.status === "waiting" ? "#f4c56a" : "#ff718b";
            context.lineWidth = 2;

            context.strokeRect(
                x * overlay.width,
                y * overlay.height,
                w * overlay.width,
                h * overlay.height
            );
        }
    } catch (error) {
        stopCamera();
        $("notice").textContent = error.message;
    } finally {
        busy = false;
        if (running) setTimeout(scan, 150);
    }
}
///////////////////////////////
// $("describe").addEventListener("click", async () => {
//     if (!running || video.readyState < 2) {
//         $("description").textContent = "Start the camera first.";
//         return;
//     }

//     const button = $("describe");
//     button.disabled = true;
//     button.textContent = "Processing...";
//     $("description").textContent = "Analyzing the live camera view...";

//     try {
//         const frame = document.createElement("canvas");
//         const ratio = Math.min(1, 640 / video.videoWidth);

//         frame.width = Math.round(video.videoWidth * ratio);
//         frame.height = Math.round(video.videoHeight * ratio);

//         // Capture du flux webcam, pas de la photo de référence.
//         frame.getContext("2d").drawImage(
//             video, 0, 0, frame.width, frame.height
//         );

//         const blob = await new Promise(resolve =>
//             frame.toBlob(resolve, "image/jpeg", 0.9)
//         );

//         if (!blob) {
//             throw new Error("Unable to capture the camera view.");
//         }

//         const form = new FormData();
//         form.append("image", blob, "webcam-frame.jpg");

//         const data = await post("/api/describe", form);

//         $("description").textContent =
//             `${data.description}\n\nWebcam capture · ${data.duration_s} s`;
//     } catch (error) {
//         $("description").textContent = error.message;
//     } finally {
//         button.disabled = false;
//         button.textContent = "Describe Live Scene";
//     }
// });
///////////////////////////////
window.addEventListener("pagehide", stopCamera);