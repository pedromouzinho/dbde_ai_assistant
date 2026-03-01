import React, { useState, useEffect, useRef } from 'react';
import {
  API_URL,
  DEFAULT_MAX_IMAGES_PER_MESSAGE,
  DEFAULT_MAX_FILES_PER_CONVERSATION,
  DEFAULT_MAX_BATCH_TOTAL_BYTES,
  UPLOAD_POLL_INTERVAL_MS,
  UPLOAD_JOB_TIMEOUT_MS,
  DEVOPS_WORKITEM_BASE_URL,
  EMPTY_CONVERSATION,
  MILLENNIUM_LOGO_DATA_URI,
} from './utils/constants.js';
import { renderMarkdown } from './utils/markdown.js';
import {
  getPreferredExportableData,
  getPreferredToolResult,
  getPreferredAutoCsvDownload,
} from './utils/toolResults.js';
import { getAuthHeaders, authFetch } from './utils/auth.js';
import ErrorBoundary from './components/ErrorBoundary.jsx';
import FeedbackWidget from './components/FeedbackWidget.jsx';
import ChartBlock from './components/ChartBlock.jsx';
import MessageBubble from './components/MessageBubble.jsx';
import TypingIndicator from './components/TypingIndicator.jsx';
import LoginScreen from './components/LoginScreen.jsx';
import UserMenu from './components/UserMenu.jsx';

function App() {
    const [conversations, setConversations] = useState([{ ...EMPTY_CONVERSATION }]);
    const [activeIdx, setActiveIdx] = useState(0);
    const [input, setInput] = useState("");
    const [loading, setLoading] = useState(false);
    const [streamingText, setStreamingText] = useState("");
    const [streamingRenderedBlocks, setStreamingRenderedBlocks] = useState([]);
    const [streamingActiveBlock, setStreamingActiveBlock] = useState("");
    const [streamingStatus, setStreamingStatus] = useState("");
    const [sidebarOpen, setSidebarOpen] = useState(window.innerWidth > 768);
    const [agentMode, setAgentMode] = useState("general");
    const [modelTier, setModelTier] = useState("standard");
    const [tierRoutingNotice, setTierRoutingNotice] = useState("");
    const [uploadedFiles, setUploadedFiles] = useState([]);
    const [imagePreviews, setImagePreviews] = useState([]);
    const [uploadingFiles, setUploadingFiles] = useState(false);
    const [uploadProgressText, setUploadProgressText] = useState("");
    const [maxImagesPerMessage, setMaxImagesPerMessage] = useState(DEFAULT_MAX_IMAGES_PER_MESSAGE);
    const [maxFilesPerConversation, setMaxFilesPerConversation] = useState(DEFAULT_MAX_FILES_PER_CONVERSATION);
    const [maxBatchTotalBytes, setMaxBatchTotalBytes] = useState(DEFAULT_MAX_BATCH_TOTAL_BYTES);
    const [uploadWorkerConcurrency, setUploadWorkerConcurrency] = useState(2);
    const [dragOver, setDragOver] = useState(false);
    const [auth, setAuth] = useState(null);
    const [authInitializing, setAuthInitializing] = useState(true);
    const [showExportDropdown, setShowExportDropdown] = useState(false);
    const [selectorOpen, setSelectorOpen] = useState(false);
    const [suggestionSeed, setSuggestionSeed] = useState(0);

    const active = conversations[activeIdx] || { ...EMPTY_CONVERSATION };
    const activeMessages = Array.isArray(active.messages) ? active.messages : [];
    const activeUploadedFiles = Array.isArray(active.uploadedFiles) ? active.uploadedFiles : uploadedFiles;
    const authUser = auth ? auth.user : {};
    const userId = authUser.username || "";

    const chatEndRef = useRef(null);
    const inputRef = useRef(null);
    const fileInputRef = useRef(null);
    const imageInputRef = useRef(null);
    const selectorRef = useRef(null);

    useEffect(() => { chatEndRef.current && chatEndRef.current.scrollIntoView({ behavior: "smooth" }); }, [conversations, activeIdx, loading, streamingText]);
    useEffect(() => {
        if (!Array.isArray(conversations) || conversations.length === 0) {
            setConversations([{ ...EMPTY_CONVERSATION }]);
            if (activeIdx !== 0) setActiveIdx(0);
            return;
        }
        if (activeIdx < 0 || activeIdx >= conversations.length) {
            setActiveIdx(0);
        }
    }, [conversations, activeIdx]);
    useEffect(() => {
        function handleClickOutside(e) {
            if (selectorRef.current && !selectorRef.current.contains(e.target)) {
                setSelectorOpen(false);
            }
        }
        document.addEventListener("mousedown", handleClickOutside);
        return () => document.removeEventListener("mousedown", handleClickOutside);
    }, []);

    function handleLogin(user) {
        const a = { user };
        setAuth(a);
    }

    async function handleLogout() {
        try {
            await authFetch(API_URL + "/api/auth/logout", { method: "POST" });
        } catch (e) {
            console.warn("Logout request failed:", e);
        }
        setAuth(null);
        setConversations([{ id: null, title: "Nova conversa", messages: [], mode: "general", uploadedFiles: [] }]);
        setActiveIdx(0);
        setUploadedFiles([]);
        setImagePreviews([]);
    }

    function authHeaders() {
        return getAuthHeaders();
    }

    // ─── Hooks that must be called before any early return ───────────────
    const saveTimerRef = useRef(null);

    useEffect(() => {
        if (userId && auth) loadChats(userId);
    }, [userId, auth]);

    useEffect(() => {
        let cancelled = false;
        async function bootstrapSession() {
            try {
                const res = await authFetch(API_URL + "/api/auth/me");
                if (!res.ok) {
                    if (!cancelled) setAuth(null);
                    return;
                }
                const me = await res.json();
                if (!cancelled) {
                    setAuth({
                        user: {
                            username: me.username,
                            role: me.role,
                            display_name: me.name || me.username,
                        },
                    });
                }
            } catch (e) {
                if (!cancelled) setAuth(null);
            } finally {
                if (!cancelled) setAuthInitializing(false);
            }
        }
        bootstrapSession();
        return () => { cancelled = true; };
    }, []);

    useEffect(() => {
        let cancelled = false;
        async function loadRuntimeLimits() {
            try {
                const res = await fetch(API_URL + "/api/info");
                if (!res.ok) return;
                const info = await res.json();
                const limits = info && info.upload_limits ? info.upload_limits : {};
                const maxImages = Number(limits.max_images_per_message);
                const maxFiles = Number(limits.max_files_per_conversation);
                const maxBatchBytes = Number(limits.max_batch_total_bytes);
                const maxConcurrency = Number(limits.max_concurrent_jobs);
                if (!cancelled && Number.isFinite(maxImages) && maxImages > 0) {
                    setMaxImagesPerMessage(Math.floor(maxImages));
                }
                if (!cancelled && Number.isFinite(maxFiles) && maxFiles > 0) {
                    setMaxFilesPerConversation(Math.floor(maxFiles));
                }
                if (!cancelled && Number.isFinite(maxBatchBytes) && maxBatchBytes > 0) {
                    setMaxBatchTotalBytes(Math.floor(maxBatchBytes));
                }
                if (!cancelled && Number.isFinite(maxConcurrency) && maxConcurrency > 0) {
                    setUploadWorkerConcurrency(Math.max(1, Math.min(4, Math.floor(maxConcurrency))));
                }
            } catch (e) {
                // Fallback silencioso para defaults locais
            }
        }
        loadRuntimeLimits();
        return () => { cancelled = true; };
    }, []);

    // ─── Auth gate ──────────────────────────────────────────────────────
    if (authInitializing) return React.createElement("div", { style: { minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", color: "#777", fontSize: 14 } }, "A iniciar sessão...");
    if (!auth) return React.createElement(LoginScreen, { onLogin: handleLogin });

    // ─── Chat persistence ────────────────────────────────────────────────
    async function loadChats(uid) {
        try {
            const res = await authFetch(API_URL + "/api/chats/" + uid, { headers: authHeaders() });
            if (res.status === 401) { handleLogout(); return; }
            if (res.ok) {
                const data = await res.json();
                if (data.chats && data.chats.length > 0) {
                    const loaded = data.chats.map(c => ({
                        id: c.conversation_id, title: c.title || "Conversa",
                        messages: [], savedOnServer: true, uploadedFiles: [],
                        message_count: c.message_count,
                    }));
                    setConversations([{ id: null, title: "Nova conversa", messages: [], mode: "general", uploadedFiles: [] }, ...loaded]);
                }
            }
        } catch (e) { console.error("Load chats error:", e); }
    }

    async function loadChatMessages(uid, convId, idx) {
        try {
            const res = await authFetch(API_URL + "/api/chats/" + uid + "/" + convId, { headers: authHeaders() });
            if (res.ok) {
                const data = await res.json();
                setConversations(prev => {
                    const u = [...prev];
                    u[idx] = { ...u[idx], messages: data.messages || [], savedOnServer: true };
                    return u;
                });
            }
        } catch (e) { console.error("Load msgs error:", e); }
    }

    function scheduleSave(conv) {
        if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
        saveTimerRef.current = setTimeout(() => saveChat(conv), 2000);
    }

    async function saveChat(conv) {
        if (!conv.id || conv.messages.length === 0) return;
        try {
            await authFetch(API_URL + "/api/chats/save", {
                method: "POST", headers: authHeaders(),
                body: JSON.stringify({ user_id: userId, conversation_id: conv.id, title: conv.title, messages: conv.messages }),
            });
        } catch (e) { console.error("Save error:", e); }
    }

    function startNew() {
        setConversations(prev => {
            if (prev[0] && prev[0].messages.length === 0) return prev;
            return [{ id: null, title: "Nova conversa", messages: [], mode: agentMode, uploadedFiles: [] }, ...prev];
        });
        setActiveIdx(0);
        setUploadedFiles([]);
        setImagePreviews([]);
        setTimeout(() => inputRef.current && inputRef.current.focus(), 100);
    }

    function deleteConv(idx) {
        const conv = conversations[idx];
        setConversations(prev => {
            const u = prev.filter((_, i) => i !== idx);
            if (u.length === 0) return [{ id: null, title: "Nova conversa", messages: [], mode: "general", uploadedFiles: [] }];
            return u;
        });
        if (idx <= activeIdx) setActiveIdx(Math.max(0, activeIdx - 1));
        // Delete from server
        if (conv.id && userId) {
            authFetch(API_URL + "/api/chats/" + userId + "/" + conv.id, { method: "DELETE", headers: authHeaders() }).catch(() => {});
        }
    }

    // ─── File upload ─────────────────────────────────────────────────────
    function sleep(ms) {
        return new Promise(resolve => setTimeout(resolve, ms));
    }

    async function uploadSingleFileSync(file, conversationId) {
        const formData = new FormData();
        formData.append("file", file);
        if (conversationId) formData.append("conversation_id", conversationId);
        const res = await authFetch(API_URL + "/upload", { method: "POST", body: formData });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || "Erro upload");
        }
        const data = await res.json();
        if (data && data.status === "queued" && data.job_id) {
            return await waitUploadJob(data.job_id);
        }
        return data;
    }

    async function queueUploadJob(file, conversationId) {
        const formData = new FormData();
        formData.append("file", file);
        if (conversationId) formData.append("conversation_id", conversationId);
        const res = await authFetch(API_URL + "/upload/async", { method: "POST", body: formData });
        if (!res.ok) {
            if (res.status === 404 || res.status === 405) {
                return null;
            }
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || "Erro ao criar job de upload");
        }
        return await res.json();
    }

    async function queueUploadJobsBatch(files, conversationId) {
        const formData = new FormData();
        files.forEach(f => formData.append("files", f));
        if (conversationId) formData.append("conversation_id", conversationId);
        const res = await authFetch(API_URL + "/upload/batch/async", { method: "POST", body: formData });
        if (!res.ok) {
            if (res.status === 404 || res.status === 405) {
                return null;
            }
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || "Erro ao criar jobs de upload");
        }
        return await res.json();
    }

    async function waitUploadJob(jobId) {
        const deadline = Date.now() + UPLOAD_JOB_TIMEOUT_MS;
        while (Date.now() < deadline) {
            const res = await authFetch(API_URL + "/api/upload/status/" + encodeURIComponent(jobId), { method: "GET" });
            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                throw new Error(err.detail || "Erro ao consultar estado do upload");
            }
            const job = await res.json();
            if (job.status === "completed") {
                if (!job.result) throw new Error("Upload concluído sem resultado");
                return job.result;
            }
            if (job.status === "failed") {
                throw new Error(job.error || "Falha no processamento do ficheiro");
            }
            await sleep(UPLOAD_POLL_INTERVAL_MS);
        }
        throw new Error("Timeout no processamento do ficheiro. Tenta novamente.");
    }

    async function fetchUploadStatusBatch(jobIds) {
        const res = await authFetch(API_URL + "/api/upload/status/batch", {
            method: "POST",
            headers: authHeaders(),
            body: JSON.stringify({ job_ids: jobIds }),
        });
        if (!res.ok) {
            if (res.status === 404 || res.status === 405) {
                return null;
            }
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || "Erro ao consultar estado batch dos uploads");
        }
        return await res.json();
    }

    async function resolveQueuedUploadsLegacy(queuedJobs, concurrency, onProgress) {
        if (!Array.isArray(queuedJobs) || queuedJobs.length === 0) {
            return { results: [], errors: [] };
        }
        const results = new Array(queuedJobs.length);
        const errors = [];
        let cursor = 0;
        let processed = 0;

        async function worker() {
            while (true) {
                const idx = cursor++;
                if (idx >= queuedJobs.length) return;
                const job = queuedJobs[idx];
                try {
                    const data = await waitUploadJob(job.job_id);
                    results[idx] = { ok: true, data, filename: job.filename };
                    processed += 1;
                    if (onProgress) onProgress(processed, queuedJobs.length, job.filename, true);
                } catch (err) {
                    const message = (err && err.message) ? err.message : "Falha no processamento do ficheiro";
                    results[idx] = { ok: false, error: message, filename: job.filename };
                    errors.push({ filename: job.filename, error: message });
                    processed += 1;
                    if (onProgress) onProgress(processed, queuedJobs.length, job.filename, false);
                }
            }
        }

        const workerCount = Math.max(1, Math.min(concurrency, queuedJobs.length));
        await Promise.all(Array.from({ length: workerCount }, () => worker()));
        return { results, errors };
    }

    async function resolveQueuedUploads(queuedJobs, concurrency, onProgress) {
        if (!Array.isArray(queuedJobs) || queuedJobs.length === 0) {
            return { results: [], errors: [] };
        }
        const byId = new Map();
        queuedJobs.forEach((job, idx) => {
            byId.set(job.job_id, { idx, filename: job.filename || "ficheiro" });
        });
        const results = new Array(queuedJobs.length);
        const errors = [];
        const pending = new Set(queuedJobs.map(j => j.job_id));
        const deadline = Date.now() + UPLOAD_JOB_TIMEOUT_MS;
        let processed = 0;

        while (pending.size > 0 && Date.now() < deadline) {
            const ids = Array.from(pending);
            let batch;
            try {
                batch = await fetchUploadStatusBatch(ids);
            } catch (err) {
                throw err;
            }
            if (!batch || !Array.isArray(batch.items)) {
                return await resolveQueuedUploadsLegacy(queuedJobs, concurrency, onProgress);
            }

            for (const item of batch.items) {
                const jobId = String(item.job_id || "");
                if (!pending.has(jobId)) continue;
                const meta = byId.get(jobId);
                if (!meta) {
                    pending.delete(jobId);
                    continue;
                }

                const status = String(item.status || "").toLowerCase();
                if (status === "completed") {
                    if (item.result) {
                        results[meta.idx] = { ok: true, data: item.result, filename: meta.filename };
                        processed += 1;
                        if (onProgress) onProgress(processed, queuedJobs.length, meta.filename, true);
                    } else {
                        const msg = "Upload concluído sem resultado";
                        results[meta.idx] = { ok: false, error: msg, filename: meta.filename };
                        errors.push({ filename: meta.filename, error: msg });
                        processed += 1;
                        if (onProgress) onProgress(processed, queuedJobs.length, meta.filename, false);
                    }
                    pending.delete(jobId);
                } else if (status === "failed" || status === "not_found" || status === "forbidden") {
                    const msg = item.error || "Falha no processamento do ficheiro";
                    results[meta.idx] = { ok: false, error: msg, filename: meta.filename };
                    errors.push({ filename: meta.filename, error: msg });
                    processed += 1;
                    if (onProgress) onProgress(processed, queuedJobs.length, meta.filename, false);
                    pending.delete(jobId);
                }
            }

            if (pending.size > 0) {
                await sleep(UPLOAD_POLL_INTERVAL_MS);
            }
        }

        for (const jobId of pending) {
            const meta = byId.get(jobId);
            if (!meta) continue;
            const msg = "Timeout no processamento do ficheiro";
            results[meta.idx] = { ok: false, error: msg, filename: meta.filename };
            errors.push({ filename: meta.filename, error: msg });
            if (onProgress) onProgress(Math.min(queuedJobs.length, ++processed), queuedJobs.length, meta.filename, false);
        }

        return { results, errors };
    }

    async function handleFileUpload(e) {
        const selectedFiles = Array.from((e.target && e.target.files) || []);
        if (selectedFiles.length === 0) return;

        const currentCount = Array.isArray(activeUploadedFiles) ? activeUploadedFiles.length : 0;
        const availableSlots = Math.max(0, maxFilesPerConversation - currentCount);
        const filesWithinSlot = selectedFiles.slice(0, availableSlots);
        const filesToUpload = [];
        const skippedByBatchLimit = [];
        let batchBytes = 0;
        for (const file of filesWithinSlot) {
            const fsize = Number(file.size || 0);
            if (batchBytes + fsize > maxBatchTotalBytes) {
                skippedByBatchLimit.push(file.name || "ficheiro");
                continue;
            }
            filesToUpload.push(file);
            batchBytes += fsize;
        }
        if (filesToUpload.length === 0) {
            if (skippedByBatchLimit.length > 0) {
                const mb = (maxBatchTotalBytes / (1024 * 1024)).toFixed(0);
                alert(`Lote excede ${mb}MB. Seleciona menos ficheiros ou ficheiros mais pequenos.`);
            } else {
                alert(`Limite de ${maxFilesPerConversation} ficheiros por conversa atingido.`);
            }
            e.target.value = "";
            return;
        }
        if (selectedFiles.length > filesToUpload.length || skippedByBatchLimit.length > 0) {
            alert(`Só foram processados ${filesToUpload.length} ficheiros (máximo por conversa: ${maxFilesPerConversation}).`);
        }

        try {
            setUploadingFiles(true);
            setUploadProgressText(`A preparar upload de ${filesToUpload.length} ficheiro(s)...`);
            let convId = active && active.id ? active.id : null;
            let lastData = null;
            const uploadedNow = [];
            const failedNow = [];
            let useAsyncJobs = true;
            const queuedJobs = [];
            const preSkipped = [];

            if (useAsyncJobs) {
                setUploadProgressText(`A enfileirar ${filesToUpload.length} ficheiro(s)...`);
                const batch = await queueUploadJobsBatch(filesToUpload, convId);
                if (batch && Array.isArray(batch.queued_jobs)) {
                    convId = batch.conversation_id || convId;
                    batch.queued_jobs.forEach(j => {
                        queuedJobs.push({
                            job_id: j.job_id,
                            filename: j.filename,
                        });
                    });
                    if (Array.isArray(batch.skipped)) {
                        batch.skipped.forEach(s => {
                            preSkipped.push({
                                filename: s.filename || "ficheiro",
                                error: s.reason || "Não enfileirado",
                            });
                        });
                    }
                } else {
                    for (const file of filesToUpload) {
                        if (!useAsyncJobs) break;
                        setUploadProgressText(`A enfileirar ${file.name}...`);
                        let queued = null;
                        try {
                            queued = await queueUploadJob(file, convId);
                        } catch (err) {
                            preSkipped.push({
                                filename: file.name || "ficheiro",
                                error: (err && err.message) ? err.message : "Não foi possível enfileirar",
                            });
                            continue;
                        }
                        if (!queued) {
                            useAsyncJobs = false;
                            queuedJobs.length = 0;
                            break;
                        }
                        convId = queued.conversation_id || convId;
                        queuedJobs.push({
                            job_id: queued.job_id,
                            filename: file.name,
                        });
                    }
                }
            }

            if (useAsyncJobs && queuedJobs.length > 0) {
                const outcomes = await resolveQueuedUploads(
                    queuedJobs,
                    uploadWorkerConcurrency,
                    (done, total, filename, ok) => {
                        const mark = ok ? "OK" : "FALHA";
                        setUploadProgressText(`A processar anexos: ${done}/${total} · ${mark} · ${filename || ""}`);
                    }
                );
                for (const outcome of outcomes.results) {
                    if (!outcome) continue;
                    if (outcome.ok && outcome.data) {
                        const data = outcome.data;
                        convId = data.conversation_id || convId;
                        lastData = data;
                        uploadedNow.push(data);
                    } else {
                        failedNow.push({
                            filename: outcome.filename || "ficheiro",
                            error: outcome.error || "Falha no processamento",
                        });
                    }
                }
            } else {
                for (const file of filesToUpload) {
                    setUploadProgressText(`A processar ${file.name}...`);
                    try {
                        const data = await uploadSingleFileSync(file, convId);
                        convId = data.conversation_id || convId;
                        lastData = data;
                        uploadedNow.push(data);
                    } catch (err) {
                        failedNow.push({
                            filename: file.name || "ficheiro",
                            error: (err && err.message) ? err.message : "Falha no processamento",
                        });
                    }
                }
            }
            failedNow.push(...preSkipped);

            if (uploadedNow.length === 0) {
                const failedSummary = failedNow.slice(0, 3).map(f => `${f.filename}: ${f.error}`).join(" | ");
                throw new Error(failedSummary ? `Nenhum ficheiro processado com sucesso. ${failedSummary}` : "Nenhum ficheiro processado com sucesso.");
            }

            const allFiles = (lastData && Array.isArray(lastData.all_files)) ? lastData.all_files : [];
            setUploadedFiles(allFiles);
            setConversations(prev => {
                const u = [...prev];
                const successDetails = uploadedNow.map(d => `• ${d.filename} (${d.rows} linhas)`).join("\n");
                const failureDetails = failedNow.length > 0
                    ? `\n\n⚠️ ${failedNow.length} ficheiro(s) com falha:\n` + failedNow.slice(0, 5).map(f => `• ${f.filename}: ${f.error}`).join("\n")
                    : "";
                u[activeIdx] = {
                    ...u[activeIdx],
                    id: (lastData && lastData.conversation_id) || u[activeIdx].id,
                    fileMode: true,
                    uploadedFiles: allFiles,
                    messages: [...u[activeIdx].messages, {
                        role: "assistant",
                        content: `📊 ${uploadedNow.length} ficheiro(s) carregado(s) com sucesso.\n\n${successDetails}${failureDetails}\n\nTotal anexado nesta conversa: ${allFiles.length}/${maxFilesPerConversation}.`,
                        tools_used: ["upload_file"],
                    }],
                };
                return u;
            });
        } catch (e) {
            alert("Erro: " + e.message);
        } finally {
            setUploadingFiles(false);
            setUploadProgressText("");
            e.target.value = "";
        }
    }

    async function getPendingUploads(conversationId) {
        if (!conversationId) return 0;
        try {
            const res = await authFetch(API_URL + "/api/upload/pending/" + encodeURIComponent(conversationId), { method: "GET" });
            if (!res.ok) return 0;
            const data = await res.json();
            return Number(data.pending_jobs || 0);
        } catch (e) {
            return 0;
        }
    }

    // ─── Image upload ────────────────────────────────────────────────────
    function addImageFiles(files) {
        const candidates = Array.from(files).filter(f => f.type.startsWith("image/"));
        const allowedSlots = Math.max(0, maxImagesPerMessage - imagePreviews.length);
        const accepted = candidates.slice(0, allowedSlots);
        if (candidates.length > accepted.length) {
            alert(`Máximo de ${maxImagesPerMessage} imagens por pedido.`);
        }
        accepted.forEach(f => {
            const reader = new FileReader();
            reader.onload = (ev) => {
                const dataUrl = ev.target.result;
                const base64 = dataUrl.split(",")[1];
                const contentType = f.type;
                setImagePreviews(prev => [...prev, { dataUrl, base64, contentType, filename: f.name, size: (f.size / 1024).toFixed(0) + "KB" }]);
            };
            reader.readAsDataURL(f);
        });
    }

    function handleImageUpload(e) { if (e.target.files) addImageFiles(e.target.files); e.target.value = ""; }

    function handlePaste(e) {
        const items = e.clipboardData && e.clipboardData.items;
        if (!items) return;
        const imageFiles = [];
        for (let i = 0; i < items.length; i++) {
            if (items[i].type.startsWith("image/")) { const f = items[i].getAsFile(); if (f) imageFiles.push(f); }
        }
        if (imageFiles.length > 0) { e.preventDefault(); addImageFiles(imageFiles); }
    }

    function removeImage(idx) { setImagePreviews(prev => prev.filter((_, i) => i !== idx)); }

    function normalizeRoutingText(value) {
        return String(value || "")
            .toLowerCase()
            .normalize("NFD")
            .replace(/[\u0300-\u036f]/g, "");
    }

    function shouldEscalateFastPrompt(question, filesCount = 0, imagesCount = 0) {
        if (filesCount > 0 || imagesCount > 0) return true;
        const q = normalizeRoutingText(question);
        if (!q) return false;
        const analyticKeywords = [
            "analisa", "analisar", "analise", "resumo estatistico", "estatistica",
            "minimo", "maximo", "media", "mediana", "percentil", "desvio padrao",
            "variancia", "correlacao", "regressao", "comparar", "comparacao",
            "tendencia", "padrao", "serie temporal", "volatilidade", "agregacao",
            "por ano", "por mes", "por semana", "por dia", "grafico", "chart",
            "plot", "scatter", "histograma", "csv", "excel", "xlsx",
            "dataset", "tabela", "ficheiro", "upload", "anexo",
        ];
        if (analyticKeywords.some((kw) => q.includes(kw))) return true;
        return /\b(min|max|avg|mean|std|p\d{2})\b/.test(q);
    }

    function handleDrop(e) {
        e.preventDefault(); setDragOver(false);
        const files = e.dataTransfer && e.dataTransfer.files;
        if (!files || files.length === 0) return;
        const imageFiles = Array.from(files).filter(f => f.type.startsWith("image/"));
        const dataFiles = Array.from(files).filter(f => !f.type.startsWith("image/"));
        if (imageFiles.length > 0) addImageFiles(imageFiles);
        if (dataFiles.length > 0) {
            const dt = new DataTransfer();
            dataFiles.forEach(f => dt.items.add(f));
            if (fileInputRef.current) { fileInputRef.current.files = dt.files; handleFileUpload({ target: fileInputRef.current }); }
        }
    }

    // ─── Mode switch ─────────────────────────────────────────────────────
    async function switchMode(newMode) {
        setAgentMode(newMode);
        if (active && active.id) {
            try { await authFetch(API_URL + "/api/mode/switch", { method: "POST", headers: authHeaders(), body: JSON.stringify({ conversation_id: active.id, mode: newMode }) }); } catch (e) { console.warn("Mode switch sync failed:", e); }
        }
        if (active) {
            const label = newMode === "userstory" ? "User Story Writer" : "Assistente Geral";
            setConversations(prev => {
                const u = [...prev];
                u[activeIdx] = { ...u[activeIdx], mode: newMode, messages: [...u[activeIdx].messages, { role: "assistant", content: `🔄 Modo alterado para **${label}**. ${newMode === "userstory" ? "Estou pronto para gerar User Stories." : "Modo geral ativo."}`, tools: [] }] };
                return u;
            });
        }
    }

    // ─── SEND MESSAGE (SSE Streaming) ────────────────────────────────────
    async function send() {
        if (!input.trim() || loading || uploadingFiles || !active) return;
        if (active.id) {
            const pendingUploads = await getPendingUploads(active.id);
            if (pendingUploads > 0) {
                alert(`Ainda existem ${pendingUploads} upload(s) a processar. Aguarda conclusão antes de enviar a pergunta.`);
                return;
            }
        }
        const q = input.trim();
        const currentImages = [...imagePreviews];
        const fastEscalatedToThinking = modelTier === "fast" && shouldEscalateFastPrompt(
            q,
            Array.isArray(activeUploadedFiles) ? activeUploadedFiles.length : 0,
            currentImages.length
        );
        const requestTier = fastEscalatedToThinking ? "standard" : modelTier;
        if (fastEscalatedToThinking) {
            setTierRoutingNotice("🧠 Pedido analítico detetado: enviado automaticamente em Thinking para melhor qualidade.");
        } else {
            setTierRoutingNotice("");
        }
        setInput(""); setImagePreviews([]);
        if (inputRef.current) inputRef.current.style.height = "auto";

        // Add user message
        setConversations(prev => {
            const u = [...prev];
            u[activeIdx] = {
                ...u[activeIdx],
                messages: [...u[activeIdx].messages, {
                    role: "user", content: q,
                    images: currentImages.length > 0 ? currentImages.map(img => ({ url: img.dataUrl, name: img.filename })) : null,
                }],
                title: u[activeIdx].messages.length === 0 ? q.slice(0, 42) + (q.length > 42 ? "..." : "") : u[activeIdx].title,
            };
            return u;
        });

        setLoading(true);
        setStreamingText("");
        setStreamingRenderedBlocks([]);
        setStreamingActiveBlock("");
        setStreamingStatus("");

        try {
            const imagesPayload = currentImages.slice(0, maxImagesPerMessage).map(img => ({
                base64: img.base64,
                content_type: img.contentType,
                filename: img.filename,
            }));
            const firstImage = imagesPayload.length > 0 ? imagesPayload[0] : null;
            const reqBody = {
                question: q,
                conversation_id: active.id || null,
                image_base64: firstImage ? firstImage.base64 : null,
                image_content_type: firstImage ? firstImage.content_type : null,
                images: imagesPayload.length > 0 ? imagesPayload : null,
                mode: agentMode,
                model_tier: requestTier,
            };

            // Try SSE streaming first
            let useStreaming = true;
            let streamCompleted = false;
            let streamedText = "";

            if (useStreaming) {
                try {
                    const res = await authFetch(API_URL + "/chat/agent/stream", {
                        method: "POST", headers: authHeaders(), body: JSON.stringify(reqBody),
                    });

                    if (!res.ok || !res.body) {
                        // Fallback to non-streaming
                        useStreaming = false;
                    } else {
                        const reader = res.body.getReader();
                        const decoder = new TextDecoder();
                        let buffer = "";
                        let fullText = "";
                        let convId = active.id;
                        let toolsUsed = [];
                        let toolDetails = [];
                        let modelUsed = "";
                        let totalTime = 0;
                        let tokensUsed = null;
                        let streamHasExportable = false;
                        let streamExportIndex = null;
                        let committedUntil = 0;

                        while (true) {
                            const { done, value } = await reader.read();
                            if (done) break;

                            buffer += decoder.decode(value, { stream: true });
                            const lines = buffer.split("\n");
                            buffer = lines.pop() || "";

                            for (const line of lines) {
                                if (!line.startsWith("data: ")) continue;
                                try {
                                    const evt = JSON.parse(line.slice(6));
                                    switch (evt.type) {
                                        case "init":
                                            convId = evt.conversation_id || convId;
                                            break;
                                        case "thinking":
                                            setStreamingStatus(evt.tool || "A pensar...");
                                            break;
                                        case "tool_start":
                                            setStreamingStatus(`🔧 ${(evt.tool || "").replace(/_/g, " ")}...`);
                                            if (evt.tool) toolsUsed.push(evt.tool);
                                            break;
                                        case "tool_result":
                                            setStreamingStatus(`✅ ${(evt.tool || "").replace(/_/g, " ")}`);
                                            break;
                                        case "token":
                                            fullText += (evt.text || "");
                                            streamedText = fullText;
                                            setStreamingText(fullText);
                                            const lastBlockBoundary = fullText.lastIndexOf("\n\n");
                                            if (lastBlockBoundary >= committedUntil) {
                                                const nextCommittedUntil = lastBlockBoundary + 2;
                                                const newlyCommitted = fullText.slice(committedUntil, nextCommittedUntil);
                                                const newBlocks = newlyCommitted
                                                    .split(/\n\n+/)
                                                    .filter(s => s && s.replace(/\s/g, "").length > 0);
                                                if (newBlocks.length > 0) {
                                                    const renderedNewBlocks = newBlocks.map(block => renderMarkdown(block));
                                                    setStreamingRenderedBlocks(prev => prev.concat(renderedNewBlocks));
                                                }
                                                committedUntil = nextCommittedUntil;
                                            }
                                            setStreamingActiveBlock(fullText.slice(committedUntil));
                                            setStreamingStatus("");
                                            break;
                                        case "done":
                                            modelUsed = evt.model_used || "";
                                            totalTime = evt.total_time_ms || 0;
                                            tokensUsed = evt.tokens_used || null;
                                            streamHasExportable = !!evt.has_exportable_data;
                                            streamExportIndex = (evt.export_index !== undefined) ? evt.export_index : null;
                                            if (Array.isArray(evt.tools_used) && evt.tools_used.length > 0) toolsUsed = evt.tools_used;
                                            if (Array.isArray(evt.tool_details) && evt.tool_details.length > 0) toolDetails = evt.tool_details;
                                            streamCompleted = true;
                                            break;
                                        case "error":
                                            throw new Error(evt.text || evt.message || "Erro de streaming");
                                    }
                                } catch (parseErr) {
                                    if (parseErr.message.includes("Erro de streaming")) throw parseErr;
                                }
                            }
                        }

                        // Add final message
                        if (fullText || streamCompleted) {
                            let toolResults = [];
                            if (toolDetails && toolDetails.length > 0) {
                                for (const td of toolDetails) {
                                    if (td.result_json) {
                                        try {
                                            toolResults.push({
                                                tool: td.tool,
                                                result: td.result_json,
                                                result_blob_ref: td.result_blob_ref || "",
                                            });
                                        } catch (e) { console.warn("Tool result parse failed (stream):", e); }
                                    }
                                }
                            }
                            setConversations(prev => {
                                const u = [...prev];
                                u[activeIdx] = {
                                    ...u[activeIdx],
                                    id: convId,
                                    messages: [...u[activeIdx].messages, {
                                        role: "assistant", content: fullText || "⚠️ O modelo não conseguiu gerar resposta. Tenta novamente ou muda para o modo Fast.",
                                        tools_used: toolsUsed.length > 0 ? [...new Set(toolsUsed)] : undefined,
                                        tool_details: toolDetails.length > 0 ? toolDetails : undefined,
                                        tool_results: toolResults.length > 0 ? toolResults : undefined,
                                        has_exportable: streamHasExportable,
                                        export_index: streamExportIndex,
                                        model_used: modelUsed, total_time_ms: totalTime, tokens_used: tokensUsed,
                                    }],
                                };
                                scheduleSave(u[activeIdx]);
                                return u;
                            });
                            streamCompleted = true;
                        }
                    }
                } catch (streamErr) {
                    console.warn("Stream error, falling back:", streamErr);
                    if (streamedText) {
                        streamedText += "\n\n⚠️ A resposta pode estar incompleta devido a um erro de comunicação.";
                        setStreamingText(streamedText);
                        setStreamingActiveBlock(streamedText);
                        streamCompleted = true;
                    } else {
                        useStreaming = false;
                    }
                }
            }

            // Fallback: non-streaming
            if (!useStreaming && !streamCompleted) {
                setStreamingStatus("A processar...");
                let res, lastErr;
                for (let attempt = 0; attempt < 3; attempt++) {
                    try {
                        res = await authFetch(API_URL + "/chat/agent", { method: "POST", headers: authHeaders(), body: JSON.stringify(reqBody) });
                        if (res.status === 429 || res.status === 502) {
                            setStreamingStatus(`⏳ Serviço ocupado, tentativa ${attempt + 2}/3...`);
                            await new Promise(r => setTimeout(r, 5000 * (attempt + 1)));
                            continue;
                        }
                        break;
                    } catch (e) { lastErr = e; if (attempt < 2) await new Promise(r => setTimeout(r, 3000)); }
                }
                if (!res) throw lastErr || new Error("Falha após 3 tentativas");
                if (!res.ok) { const err = await res.json().catch(() => ({})); throw new Error(err.detail || "Erro " + res.status); }
                const data = await res.json();
                setConversations(prev => {
                    const u = [...prev];
                    // Build tool_results for export
                    let toolResults = [];
                    if (data.tool_details) {
                        for (const td of data.tool_details) {
                            if (td.result_json) {
                                try {
                                    toolResults.push({
                                        tool: td.tool,
                                        result: td.result_json,
                                        result_blob_ref: td.result_blob_ref || "",
                                    });
                                } catch (e) { console.warn("Tool result parse failed (sync):", e); }
                            }
                        }
                    }
                    u[activeIdx] = {
                        ...u[activeIdx],
                        id: data.conversation_id,
                        messages: [...u[activeIdx].messages, {
                            role: "assistant", content: data.answer,
                            tools_used: data.tools_used, tool_details: data.tool_details,
                            tool_results: toolResults.length > 0 ? toolResults : undefined,
                            model_used: data.model_used, tokens_used: data.tokens_used, total_time_ms: data.total_time_ms,
                            has_exportable: data.has_exportable_data || false,
                            export_index: data.export_index,
                        }],
                    };
                    scheduleSave(u[activeIdx]);
                    return u;
                });
            }
        } catch (err) {
            setConversations(prev => {
                const u = [...prev];
                u[activeIdx] = {
                    ...u[activeIdx],
                    messages: [...u[activeIdx].messages, { role: "assistant", content: "❌ Erro: " + err.message + ". Tenta novamente." }],
                };
                return u;
            });
        } finally {
            setLoading(false);
            setStreamingText("");
            setStreamingRenderedBlocks([]);
            setStreamingActiveBlock("");
            setStreamingStatus("");
            setTimeout(() => inputRef.current && inputRef.current.focus(), 100);
        }
    }

    // ─── Data export ─────────────────────────────────────────────────────
    function getAllChatMessages() {
        if (!active || !Array.isArray(active.messages)) return [];
        return active.messages
            .filter(m => m && (m.role === "user" || m.role === "assistant"))
            .map(m => ({
                role: m.role,
                content: (typeof m.content === "string" || Array.isArray(m.content)) ? m.content : (m.text || ""),
                timestamp: m.timestamp || m.created_at || "",
            }));
    }

    async function exportChat(format = "html") {
        if (!active) return;
        try {
            const messages = getAllChatMessages();
            if (!messages.length) {
                alert("Sem mensagens para exportar.");
                return;
            }
            const res = await authFetch(API_URL + "/api/export-chat", {
                method: "POST",
                headers: authHeaders(),
                body: JSON.stringify({
                    messages,
                    format,
                    title: active.title || "Chat Export",
                }),
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.detail || data.error || "Erro export chat");
            if (data.url) {
                const target = String(data.url || "");
                const finalUrl = target.startsWith("http") ? target : (API_URL + target);
                try {
                    const parsed = new URL(finalUrl, window.location.origin);
                    const apiOrigin = new URL(API_URL || window.location.origin, window.location.origin).origin;
                    const allowedOrigins = new Set([window.location.origin, apiOrigin]);
                    if ((parsed.protocol === "http:" || parsed.protocol === "https:") && allowedOrigins.has(parsed.origin)) {
                        window.open(parsed.href, "_blank", "noopener,noreferrer");
                    } else {
                        throw new Error("Origem de export não permitida");
                    }
                } catch (err) {
                    throw new Error("URL de export inválida");
                }
            }
            if (data.format_served && data.format_requested && data.format_served !== data.format_requested) {
                alert("Aviso: exportado como " + data.format_served.toUpperCase() + " em vez de " + data.format_requested.toUpperCase() + (data.fallback_reason ? " (" + data.fallback_reason + ")" : ""));
            } else if (data.note) {
                alert(data.note);
            }
        } catch (e) {
            alert("Erro ao exportar conversa: " + e.message);
        } finally {
            setShowExportDropdown(false);
        }
    }

    function _messageTextContent(msg) {
        if (!msg) return "";
        if (typeof msg.content === "string") return msg.content;
        if (Array.isArray(msg.content)) {
            return msg.content
                .filter(p => p && typeof p === "object" && p.type === "text")
                .map(p => String(p.text || ""))
                .join("\n")
                .trim();
        }
        return "";
    }

    function _promptForAssistantMessage(messages, assistantIndex) {
        if (!Array.isArray(messages) || assistantIndex < 0) return "";
        for (let i = assistantIndex - 1; i >= 0; i--) {
            const msg = messages[i];
            if (msg && msg.role === "user") return _messageTextContent(msg);
        }
        return "";
    }

    async function exportData(format) {
        if (!active) return;
        try {
            // Find the last tool result data in the conversation messages
            let selectedToolResult = null;
            let toolData = null;
            let promptSummary = "";
            for (let i = active.messages.length - 1; i >= 0; i--) {
                const msg = active.messages[i];
                selectedToolResult = getPreferredToolResult(msg.tool_results, msg.export_index);
                toolData = getPreferredExportableData(msg.tool_results, msg.export_index);
                if (selectedToolResult || toolData) {
                    promptSummary = _promptForAssistantMessage(active.messages, i);
                    break;
                }
            }
            if (!selectedToolResult && !toolData) { alert("Sem dados exportáveis nesta conversa. Executa uma query primeiro."); return; }

            const res = await authFetch(API_URL + "/api/export", {
                method: "POST", headers: authHeaders(),
                body: JSON.stringify({
                    conversation_id: active.id || "",
                    format,
                    title: active.title || "Export DBDE",
                    data: toolData || undefined,
                    result_blob_ref: selectedToolResult?.result_blob_ref || undefined,
                    summary: promptSummary || undefined,
                }),
            });
            if (res.status === 202) {
                const queued = await res.json();
                const statusEndpoint = queued.status_endpoint || (`/api/export/status/${queued.job_id}`);
                const result = await waitForExportJob(statusEndpoint);
                if (!result || !result.endpoint) throw new Error("Export concluído sem ficheiro disponível");
                await downloadGeneratedFile(result);
                return;
            }
            if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.detail || "Erro export"); }
            const blob = await res.blob();
            const ext = format === "xlsx" ? "xlsx" : format === "pdf" ? "pdf" : format === "svg" ? "svg" : format === "html" ? "html" : format === "zip" ? "zip" : "csv";
            const a = document.createElement("a");
            a.href = URL.createObjectURL(blob);
            a.download = `${(active.title || "export").replace(/[^a-zA-Z0-9]/g, "_").slice(0, 30)}.${ext}`;
            a.click();
            URL.revokeObjectURL(a.href);
        } catch (e) { alert("Erro ao exportar: " + e.message); }
    }

    async function exportMessageData(format, toolResults, exportIndex = null, messageIndex = null, withCompanion = true) {
        if (!active || !toolResults || toolResults.length === 0) return;
        try {
            const selectedToolResult = getPreferredToolResult(toolResults, exportIndex);
            const toolData = getPreferredExportableData(toolResults, exportIndex);
            if (!selectedToolResult && !toolData) { alert("Sem dados exportáveis nesta mensagem."); return; }
            const promptSummary = Number.isInteger(messageIndex) ? _promptForAssistantMessage(active.messages, messageIndex) : "";

            const res = await authFetch(API_URL + "/api/export", {
                method: "POST", headers: authHeaders(),
                body: JSON.stringify({
                    conversation_id: active.id || "",
                    format,
                    title: active.title || "Export DBDE",
                    data: toolData || undefined,
                    result_blob_ref: selectedToolResult?.result_blob_ref || undefined,
                    summary: promptSummary || undefined,
                }),
            });
            if (res.status === 202) {
                const queued = await res.json();
                const statusEndpoint = queued.status_endpoint || (`/api/export/status/${queued.job_id}`);
                const result = await waitForExportJob(statusEndpoint);
                if (!result || !result.endpoint) throw new Error("Export concluído sem ficheiro disponível");
                await downloadGeneratedFile(result);
            } else {
                if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.detail || "Erro export"); }
                const blob = await res.blob();
                const ext = format === "xlsx" ? "xlsx" : format === "pdf" ? "pdf" : format === "svg" ? "svg" : format === "html" ? "html" : format === "zip" ? "zip" : "csv";
                const a = document.createElement("a");
                a.href = URL.createObjectURL(blob);
                a.download = `${(active.title || "export").replace(/[^a-zA-Z0-9]/g, "_").slice(0, 30)}.${ext}`;
                a.click();
                URL.revokeObjectURL(a.href);
            }
            const fullCsv = getPreferredAutoCsvDownload(toolResults, exportIndex);
            if (withCompanion && fullCsv && format !== "csv") {
                await downloadGeneratedFile(fullCsv);
            }
        } catch (e) { alert("Erro ao exportar: " + e.message); }
    }

    async function exportMessageBundle(toolResults, exportIndex = null, messageIndex = null) {
        await exportMessageData("zip", toolResults, exportIndex, messageIndex, false);
    }

    async function waitForExportJob(statusEndpoint, timeoutMs = 180000) {
        const started = Date.now();
        const endpoint = String(statusEndpoint || "").startsWith("http")
            ? String(statusEndpoint)
            : (API_URL + String(statusEndpoint || ""));
        while (Date.now() - started < timeoutMs) {
            const res = await authFetch(endpoint, { method: "GET", headers: authHeaders() });
            if (!res.ok) {
                const e = await res.json().catch(() => ({}));
                throw new Error(e.detail || "Falha ao obter estado do export");
            }
            const job = await res.json();
            const status = String(job.status || "").toLowerCase();
            if (status === "completed") return job.result || {};
            if (status === "failed") throw new Error(job.error || "Export falhou");
            await new Promise(resolve => setTimeout(resolve, 1800));
        }
        throw new Error("Timeout no processamento do export");
    }

    async function downloadGeneratedFile(fileMeta) {
        if (!fileMeta) return;
        try {
            if (!fileMeta.endpoint) throw new Error("Endpoint de download em falta");
            const url = String(fileMeta.endpoint).startsWith("http") ? fileMeta.endpoint : (API_URL + fileMeta.endpoint);
            const res = await authFetch(url, { method: "GET", headers: authHeaders() });
            if (!res.ok) {
                const e = await res.json().catch(() => ({}));
                throw new Error(e.detail || "Erro download");
            }
            const blob = await res.blob();
            const fallbackName = `download_${Date.now()}.${String(fileMeta.format || "bin").toLowerCase()}`;
            const a = document.createElement("a");
            a.href = URL.createObjectURL(blob);
            a.download = fileMeta.filename || fallbackName;
            a.click();
            URL.revokeObjectURL(a.href);
        } catch (e) {
            alert("Erro ao descarregar: " + e.message);
        }
    }

    async function submitFeedback(convId, msgIdx, rating, note) {
        try {
            await authFetch(API_URL + "/feedback", { method: "POST", headers: authHeaders(), body: JSON.stringify({ conversation_id: convId, message_index: msgIdx, rating, note: note || "" }) });
        } catch (e) { console.warn("Submit feedback failed:", e); }
    }

    function handleKeyDown(e) {
        if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
    }

    const suggestionPools = {
        general: {
            rag: [
                "Quantas user stories existem na área RevampFEE?",
                "Quais bugs estão ativos no MDSE?",
                "Mostra os work items criados esta semana",
                "Lista as user stories em estado Active",
                "Quais áreas têm mais bugs abertos?",
            ],
            knowledge: [
                "Como fazer uma transferência SPIN no MSE?",
                "O que é o processo de KYC no Millennium?",
                "Explica o fluxo de abertura de conta digital",
                "Quais são as regras de compliance para SEPA?",
            ],
            analytics: [
                "Quem criou mais user stories este mês?",
                "Mostra KPIs da equipa MDSE no último sprint",
                "Qual a velocity média da área RevampFEE?",
                "Gera um gráfico de bugs por prioridade",
            ],
            files: [
                "Analisa o ficheiro Excel que vou anexar",
                "Compara os dados dos 2 ficheiros CSV",
                "Resume o conteúdo do PDF anexado",
            ],
        },
        userstory: {
            creation: [
                "Gera 3 user stories sobre pagamento de serviços",
                "Cria uma US para exportar PDF nas consultas",
                "Gera user stories para abertura de conta online",
                "Cria user stories para o módulo de notificações push",
                "Gera uma US para autenticação biométrica",
            ],
            modification: [
                "Quero adicionar um campo de IBAN no formulário",
                "Preciso de uma US para alterar o layout do dashboard",
                "Cria uma US para adicionar filtros na pesquisa",
            ],
            integration: [
                "Gera US para integração com sistema de pagamentos",
                "Cria user stories para a API de consulta de saldos",
                "US para integrar notificações por email",
            ],
        },
    };
    function getDynamicSuggestions(mode, convList, filesList) {
        const pool = suggestionPools[mode] || suggestionPools.general;
        const allSuggestions = Object.values(pool).flat();

        const recentMessages = convList
            .flatMap(c => c.messages || [])
            .filter(m => m.role === "user")
            .slice(-20)
            .map(m => (m.content || m.text || "").toLowerCase());

        const usedCategories = new Set();
        for (const msg of recentMessages) {
            for (const [cat, items] of Object.entries(pool)) {
                if (items.some(s => msg.includes(s.slice(0, 20).toLowerCase()))) {
                    usedCategories.add(cat);
                }
            }
        }

        const hasFiles = filesList && filesList.length > 0;
        const selected = [];
        const categories = Object.keys(pool);

        if (hasFiles && pool.files) {
            const fileSugg = pool.files[Math.floor(Math.random() * pool.files.length)];
            selected.push(fileSugg);
        }

        const shuffledCats = categories.sort(() => Math.random() - 0.5);
        for (const cat of shuffledCats) {
            if (selected.length >= 4) break;
            if (hasFiles && cat === "files") continue;
            const catItems = pool[cat].filter(s => !selected.includes(s));
            if (catItems.length > 0) {
                selected.push(catItems[Math.floor(Math.random() * catItems.length)]);
            }
        }

        while (selected.length < 4 && allSuggestions.length > 0) {
            const remaining = allSuggestions.filter(s => !selected.includes(s));
            if (remaining.length === 0) break;
            selected.push(remaining[Math.floor(Math.random() * remaining.length)]);
        }

        return selected.slice(0, 4);
    }
    const tierLabels = { fast: "⚡ Fast", standard: "🧠 Thinking", pro: "🔬 Pro" };
    const modeLabels = { general: "💬 Geral", userstory: "📋 User Stories" };
    const selectorLabel = `${tierLabels[modelTier]} · ${modeLabels[agentMode]}`;
    const suggestions = getDynamicSuggestions(agentMode, conversations, activeUploadedFiles);
    const showFastAnalyticHint = modelTier === "fast" && shouldEscalateFastPrompt(
        input,
        Array.isArray(activeUploadedFiles) ? activeUploadedFiles.length : 0,
        imagePreviews.length
    );

    // ─── Render ──────────────────────────────────────────────────────────

    return React.createElement("div", {
        style: { display: "flex", height: "100vh", overflow: "hidden", background: "#EAE4DC" }
    },

        // ─── Sidebar ─────────────────────────────────────────────────────
        React.createElement("div", {
            style: {
                width: sidebarOpen ? 300 : 0, minWidth: sidebarOpen ? 300 : 0,
                background: "#FAFAF8", borderRight: "1px solid rgba(0,0,0,0.06)",
                display: "flex", flexDirection: "column",
                transition: "all 0.3s cubic-bezier(0.4, 0, 0.2, 1)", overflow: "hidden",
            }
        },
            // Sidebar header
            React.createElement("div", { style: { padding: "24px 24px 20px", borderBottom: "1px solid rgba(0,0,0,0.06)" } },
                React.createElement("div", { style: { display: "flex", alignItems: "center", gap: 12, marginBottom: 20 } },
                    React.createElement("img", {
                        src: MILLENNIUM_LOGO_DATA_URI, alt: "Millennium",
                        style: { width: 38, height: 38, borderRadius: 10, flexShrink: 0 }
                    }),
                    React.createElement("div", null,
                        React.createElement("div", { style: { fontWeight: 700, fontSize: 14, color: "#1a1a1a", lineHeight: 1.2 } }, "Assistente AI"),
                        React.createElement("div", { style: { fontSize: 10, color: "#aaa", fontWeight: 500, marginTop: 2, letterSpacing: "0.3px" } }, "DBDE v7.3.0")
                    )
                ),
                React.createElement("button", {
                    className: "new-conv-btn",
                    onClick: startNew,
                    style: {
                        width: "100%", padding: "11px 16px", borderRadius: 12,
                        border: "1.5px solid rgba(0,0,0,0.12)",
                        background: "white", color: "#1a1a1a",
                        fontWeight: 600, fontSize: 13, cursor: "pointer",
                        display: "flex", alignItems: "center", justifyContent: "center",
                        gap: 8, transition: "all 0.2s ease",
                        fontFamily: "'Montserrat', sans-serif",
                        boxShadow: "0 1px 3px rgba(0,0,0,0.04)",
                    }
                }, "+ Nova Interação")
            ),

            // Conversation list
            React.createElement("div", { style: { flex: 1, overflowY: "auto", padding: "10px 12px" } },
                conversations.map((conv, i) =>
                    React.createElement("div", {
                        key: i,
                        className: "sidebar-item" + (i === activeIdx ? " active" : ""),
                        onClick: () => {
                            setActiveIdx(i);
                            setUploadedFiles(Array.isArray(conv.uploadedFiles) ? conv.uploadedFiles : []);
                            if (conv.savedOnServer && (!Array.isArray(conv.messages) || conv.messages.length === 0) && conv.id && userId) loadChatMessages(userId, conv.id, i);
                            inputRef.current && inputRef.current.focus();
                        },
                        style: {
                            padding: "12px 14px", borderRadius: 12, marginBottom: 4,
                            cursor: "pointer", display: "flex", alignItems: "center", gap: 10,
                            borderLeft: "3px solid transparent",
                            background: i === activeIdx ? "rgba(222,49,99,0.06)" : "transparent",
                        }
                    },
                        React.createElement("div", {
                            style: {
                                width: 32, height: 32, borderRadius: 8,
                                background: i === activeIdx ? "rgba(222,49,99,0.1)" : "rgba(0,0,0,0.04)",
                                display: "flex", alignItems: "center", justifyContent: "center",
                                fontSize: 13, flexShrink: 0,
                                color: i === activeIdx ? "#DE3163" : "#999",
                                transition: "all 0.2s ease",
                            }
                        }, "💬"),
                        React.createElement("div", { style: { flex: 1, minWidth: 0 } },
                            React.createElement("div", {
                                style: {
                                    fontSize: 13, fontWeight: i === activeIdx ? 600 : 400,
                                    color: i === activeIdx ? "#1a1a1a" : "#555",
                                    whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
                                }
                            }, conv.title),
                            React.createElement("div", { style: { fontSize: 11, color: "#bbb", marginTop: 1 } },
                                (Array.isArray(conv.messages) && conv.messages.length > 0)
                                    ? conv.messages.length + " msgs"
                                    : (conv.message_count ? conv.message_count + " msgs" : "Vazia")
                            )
                        ),
                        conversations.length > 1 && React.createElement("button", {
                            className: "del-btn",
                            onClick: (e) => { e.stopPropagation(); deleteConv(i); },
                            style: {
                                background: "none", border: "none", cursor: "pointer",
                                color: "transparent", padding: 4, fontSize: 14,
                                transition: "all 0.2s ease", borderRadius: 6,
                            },
                            onMouseEnter: e => { e.currentTarget.style.color = "#DE3163"; },
                            onMouseLeave: e => { e.currentTarget.style.color = "transparent"; },
                        }, "×")
                    )
                )
            ),

            // Sidebar footer
            React.createElement("div", { style: { padding: "14px 20px", borderTop: "1px solid rgba(0,0,0,0.06)", display: "flex", alignItems: "center", justifyContent: "space-between" } },
                React.createElement("span", { style: { fontSize: 11, color: "#bbb", fontWeight: 500 } }, userId),
                React.createElement("button", {
                    onClick: handleLogout,
                    style: {
                        background: "none", border: "none", fontSize: 11,
                        color: "#bbb", cursor: "pointer",
                        fontFamily: "'Montserrat', sans-serif",
                        transition: "color 0.2s",
                    },
                    onMouseEnter: e => { e.currentTarget.style.color = "#DE3163"; },
                    onMouseLeave: e => { e.currentTarget.style.color = "#bbb"; },
                }, "Sair")
            )
        ),

        // ─── Main Area ──────────────────────────────────────────────────
        React.createElement("div", {
            style: {
                flex: 1, display: "flex", flexDirection: "column",
                minWidth: 0, position: "relative",
                background: "#EAE4DC",
            },
            onDragOver: e => { e.preventDefault(); setDragOver(true); },
            onDragLeave: e => { if (e.currentTarget === e.target || !e.currentTarget.contains(e.relatedTarget)) setDragOver(false); },
            onDrop: handleDrop,
        },
            // Drag overlay
            dragOver && React.createElement("div", {
                style: {
                    position: "absolute", inset: 0, zIndex: 100,
                    background: "rgba(222,49,99,0.06)",
                    border: "3px dashed rgba(222,49,99,0.3)",
                    borderRadius: 20,
                    display: "flex", alignItems: "center", justifyContent: "center",
                    pointerEvents: "none",
                    backdropFilter: "blur(4px)",
                }
            },
                React.createElement("div", { style: { background: "white", borderRadius: 20, padding: "32px 48px", boxShadow: "0 12px 40px rgba(0,0,0,0.1)", textAlign: "center", border: "1px solid rgba(0,0,0,0.06)" } },
                    React.createElement("div", { style: { fontSize: 40, marginBottom: 8 } }, "📎"),
                    React.createElement("div", { style: { fontSize: 16, fontWeight: 600, color: "#1a1a1a" } }, "Larga aqui para anexar"),
                    React.createElement("div", { style: { fontSize: 12, color: "#999", marginTop: 6 } }, "Imagens, Excel, CSV, PDF")
                )
            ),

            // ─── Header ──────────────────────────────────────────────────
            React.createElement("div", {
                style: {
                    background: "white", borderBottom: "1px solid rgba(0,0,0,0.06)",
                    padding: "14px 28px",
                    display: "flex", alignItems: "center", justifyContent: "space-between",
                }
            },
                React.createElement("div", { style: { display: "flex", alignItems: "center", gap: 12 } },
                    React.createElement("button", {
                        onClick: () => setSidebarOpen(!sidebarOpen),
                        style: {
                            background: "none", border: "none", cursor: "pointer",
                            color: "#999", fontSize: 18, padding: "4px 8px",
                            borderRadius: 8, transition: "all 0.2s",
                        },
                        onMouseEnter: e => { e.currentTarget.style.background = "rgba(0,0,0,0.04)"; e.currentTarget.style.color = "#666"; },
                        onMouseLeave: e => { e.currentTarget.style.background = "none"; e.currentTarget.style.color = "#999"; },
                    }, sidebarOpen ? "◀" : "☰"),
                    React.createElement("div", { style: { marginLeft: 4 } },
                        React.createElement("div", { style: { fontWeight: 600, fontSize: 15, color: "#1a1a1a", letterSpacing: "-0.2px" } }, active ? active.title : "Assistente AI DBDE"),
                        React.createElement("div", { style: { fontSize: 11, color: "#bbb", marginTop: 1, fontWeight: 500 } },
                            (agentMode === "userstory" ? "📋 User Stories" : "💬 Geral") + " · " + activeMessages.length + " msgs"
                        )
                    )
                ),

                React.createElement("div", { style: { display: "flex", alignItems: "center", gap: 8 } },
                    React.createElement("div", {
                        ref: selectorRef,
                        style: { position: "relative" }
                    },
                        React.createElement("button", {
                            className: "selector-trigger",
                            onClick: () => setSelectorOpen(!selectorOpen),
                            style: {
                                display: "flex", alignItems: "center", gap: 8,
                                padding: "8px 16px", borderRadius: 12,
                                border: "1.5px solid rgba(0,0,0,0.08)", background: "white",
                                cursor: "pointer", fontSize: 12, fontWeight: 600,
                                fontFamily: "'Montserrat', sans-serif", color: "#444",
                                transition: "all 0.2s ease",
                                boxShadow: "0 1px 3px rgba(0,0,0,0.04)",
                                ...(selectorOpen ? { borderColor: "#DE3163", color: "#DE3163", boxShadow: "0 1px 3px rgba(222,49,99,0.1)" } : {})
                            }
                        },
                            React.createElement("span", null, selectorLabel),
                            React.createElement("span", {
                                style: { fontSize: 10, marginLeft: 2, transition: "transform 0.2s", transform: selectorOpen ? "rotate(180deg)" : "rotate(0)" }
                            }, "▾")
                        ),
                        selectorOpen && React.createElement("div", {
                            style: {
                                position: "absolute", right: 0, top: "calc(100% + 8px)",
                                background: "white", borderRadius: 16,
                                boxShadow: "0 12px 40px rgba(0,0,0,0.12), 0 1px 3px rgba(0,0,0,0.06)",
                                padding: 14, minWidth: 220, zIndex: 1000,
                                border: "1px solid rgba(0,0,0,0.06)",
                                animation: "fadeUp 0.15s ease",
                            }
                        },
                            React.createElement("div", {
                                style: { fontSize: 10, fontWeight: 700, color: "#999", textTransform: "uppercase", letterSpacing: 0.5, marginBottom: 6, padding: "0 4px" }
                            }, "Modelo"),
                            ["fast", "standard", "pro"].map(t =>
                                React.createElement("button", {
                                    key: t,
                                    onClick: () => { setTierRoutingNotice(""); setModelTier(t); },
                                    style: {
                                        display: "flex", alignItems: "center", gap: 8,
                                        width: "100%", padding: "8px 10px", border: "none",
                                        borderRadius: 8, cursor: "pointer", fontSize: 12,
                                        fontFamily: "'Montserrat', sans-serif",
                                        fontWeight: modelTier === t ? 700 : 400,
                                        background: modelTier === t ? "#FFF0F3" : "transparent",
                                        color: modelTier === t ? "#DE3163" : "#444",
                                        transition: "all 0.15s"
                                    },
                                    onMouseEnter: e => { if (modelTier !== t) e.currentTarget.style.background = "#f5f5f5"; },
                                    onMouseLeave: e => { if (modelTier !== t) e.currentTarget.style.background = "transparent"; },
                                },
                                    React.createElement("span", null, tierLabels[t]),
                                    modelTier === t && React.createElement("span", { style: { marginLeft: "auto", fontSize: 11 } }, "✓")
                                )
                            ),
                            React.createElement("div", { style: { borderTop: "1px solid #f0f0f0", margin: "8px 0" } }),
                            React.createElement("div", {
                                style: { fontSize: 10, fontWeight: 700, color: "#999", textTransform: "uppercase", letterSpacing: 0.5, marginBottom: 6, padding: "0 4px" }
                            }, "Modo"),
                            ["general", "userstory"].map(m =>
                                React.createElement("button", {
                                    key: m,
                                    onClick: () => { switchMode(m); setSelectorOpen(false); },
                                    style: {
                                        display: "flex", alignItems: "center", gap: 8,
                                        width: "100%", padding: "8px 10px", border: "none",
                                        borderRadius: 8, cursor: "pointer", fontSize: 12,
                                        fontFamily: "'Montserrat', sans-serif",
                                        fontWeight: agentMode === m ? 700 : 400,
                                        background: agentMode === m ? "#FFF0F3" : "transparent",
                                        color: agentMode === m ? "#DE3163" : "#444",
                                        transition: "all 0.15s"
                                    },
                                    onMouseEnter: e => { if (agentMode !== m) e.currentTarget.style.background = "#f5f5f5"; },
                                    onMouseLeave: e => { if (agentMode !== m) e.currentTarget.style.background = "transparent"; },
                                },
                                    React.createElement("span", null, modeLabels[m]),
                                    agentMode === m && React.createElement("span", { style: { marginLeft: "auto", fontSize: 11 } }, "✓")
                                )
                            )
                        )
                    ),
                    React.createElement("div", { style: { position: "relative" } },
                        React.createElement("button", {
                            className: "export-btn",
                            onClick: () => setShowExportDropdown(!showExportDropdown),
                            title: "Exportar conversa",
                            style: {
                                background: "none",
                                border: "1.5px solid rgba(0,0,0,0.08)",
                                borderRadius: 12, padding: "8px 16px",
                                cursor: "pointer", fontSize: 12, fontWeight: 600,
                                color: "#666", fontFamily: "'Montserrat', sans-serif",
                                display: "flex", alignItems: "center", gap: 6,
                                transition: "all 0.2s ease",
                                boxShadow: "0 1px 3px rgba(0,0,0,0.04)",
                            }
                        }, "⬇ Exportar"),
                        showExportDropdown && React.createElement("div", {
                            style: { position: "absolute", right: 0, top: 36, background: "white", border: "1px solid #e5e7eb", borderRadius: 10, boxShadow: "0 8px 24px rgba(0,0,0,0.12)", zIndex: 60, minWidth: 180, overflow: "hidden" }
                        },
                            React.createElement("button", {
                                onClick: () => exportChat("html"),
                                style: { width: "100%", border: "none", background: "white", padding: "10px 12px", textAlign: "left", cursor: "pointer", fontSize: 12 }
                            }, "Exportar como HTML"),
                            React.createElement("button", {
                                onClick: () => exportChat("pdf"),
                                style: { width: "100%", border: "none", borderTop: "1px solid #f1f5f9", background: "white", padding: "10px 12px", textAlign: "left", cursor: "pointer", fontSize: 12 }
                            }, "Exportar como PDF")
                        )
                    ),
                    // User menu
                    React.createElement(UserMenu, { user: authUser, onLogout: handleLogout })
                )
            ),

            // ─── Chat area ────────────────────────────────────────────────
            React.createElement("div", {
                style: { flex: 1, overflowY: "auto", padding: "28px 28px 12px", background: "#EAE4DC" }
            },
                React.createElement("div", { style: { maxWidth: 900, margin: "0 auto" } },

                    // Welcome screen
                    (!active || activeMessages.length === 0) && !loading &&
                    React.createElement("div", { style: { textAlign: "center", paddingTop: "10vh", animation: "slideUp 0.6s cubic-bezier(0.4, 0, 0.2, 1)" } },
                        React.createElement("img", {
                            src: MILLENNIUM_LOGO_DATA_URI, alt: "Millennium",
                            style: {
                                width: 72, height: 72, borderRadius: 20,
                                margin: "0 auto 24px",
                                display: "block",
                                boxShadow: "0 8px 32px rgba(0,0,0,0.1)",
                            }
                        }),
                        React.createElement("div", {
                            style: {
                                fontSize: 26, fontWeight: 700, color: "#1a1a1a",
                                marginBottom: 8, letterSpacing: "-0.5px",
                            }
                        },
                            agentMode === "userstory" ? "User Story Writer" : "Assistente AI DBDE"
                        ),
                        React.createElement("div", { style: { fontSize: 15, color: "#999", marginBottom: 40, fontWeight: 400 } },
                            agentMode === "userstory" ? "Descreve a funcionalidade para gerar User Stories" : "Como posso ajudar?"
                        ),
                        React.createElement("div", {
                            style: {
                                display: "grid",
                                gridTemplateColumns: "repeat(2, 1fr)",
                                gap: 12, maxWidth: 560,
                                margin: "0 auto",
                            }
                        },
                            suggestions.map(q =>
                                React.createElement("button", {
                                    key: q, className: "suggestion-btn",
                                    onClick: () => { setInput(q); setTimeout(() => inputRef.current && inputRef.current.focus(), 50); },
                                    style: {
                                        background: "white", border: "1px solid rgba(0,0,0,0.08)",
                                        borderRadius: 14, padding: "16px 18px",
                                        fontSize: 13, color: "#555", cursor: "pointer",
                                        textAlign: "left", transition: "all 0.2s ease",
                                        lineHeight: 1.5, fontFamily: "'Montserrat', sans-serif",
                                        boxShadow: "0 1px 3px rgba(0,0,0,0.04)",
                                        display: "flex", alignItems: "flex-start",
                                    }
                                }, q)
                            )
                        ),
                        React.createElement("button", {
                            onClick: () => setSuggestionSeed(s => s + 1),
                            style: {
                                marginTop: 20, background: "none", border: "none",
                                cursor: "pointer", fontSize: 12, color: "#bbb",
                                fontFamily: "'Montserrat', sans-serif",
                                fontWeight: 500,
                                transition: "all 0.2s ease",
                                padding: "6px 12px", borderRadius: 8,
                            },
                            onMouseEnter: e => { e.currentTarget.style.color = "#DE3163"; e.currentTarget.style.background = "rgba(222,49,99,0.04)"; },
                            onMouseLeave: e => { e.currentTarget.style.color = "#bbb"; e.currentTarget.style.background = "none"; },
                        }, "↻ Outras sugestões")
                    ),

                    // Messages
                    active && activeMessages.map((msg, i) =>
                        React.createElement(ErrorBoundary, { key: "message-boundary-" + i, name: "MessageBubble" },
                            React.createElement(MessageBubble, {
                                message: msg,
                                isLastAssistant: msg.role === "assistant" && i === activeMessages.length - 1 && !loading,
                                conversationId: active.id, messageIndex: i,
                                onFeedback: submitFeedback,
                                onExport: exportMessageData,
                                onExportBundle: exportMessageBundle,
                                onFileDownload: downloadGeneratedFile,
                            })
                        )
                    ),

                    // Streaming in progress
                    loading && (streamingText || streamingRenderedBlocks.length > 0 || streamingActiveBlock) && React.createElement("div", {
                        style: { display: "flex", alignItems: "flex-start", gap: 12, marginBottom: 16, animation: "fadeUp 0.3s ease" }
                    },
                        React.createElement("img", {
                            src: MILLENNIUM_LOGO_DATA_URI, alt: "Millennium",
                            style: { width: 32, height: 32, borderRadius: 10, flexShrink: 0 }
                        }),
                        React.createElement("div", {
                            className: "msg-content",
                            style: { background: "white", borderRadius: "4px 16px 16px 16px", padding: "14px 20px", boxShadow: "0 1px 3px rgba(0,0,0,0.06)", fontSize: 14, lineHeight: 1.7, color: "#1a1a1a", maxWidth: "min(900px, 100%)" }
                        },
                            streamingRenderedBlocks.map((blockHtml, bi) =>
                                React.createElement("div", {
                                    key: "stream-block-" + bi,
                                    dangerouslySetInnerHTML: { __html: blockHtml },
                                })
                            ),
                            streamingActiveBlock && React.createElement("div", {
                                dangerouslySetInnerHTML: { __html: renderMarkdown(streamingActiveBlock) },
                            })
                        )
                    ),

                    // Loading indicator (before tokens arrive)
                    loading && !streamingText && React.createElement(TypingIndicator, { text: streamingStatus }),

                    React.createElement("div", { ref: chatEndRef })
                )
            ),

            // ─── Input area ───────────────────────────────────────────────
            React.createElement("div", { style: { background: "white", borderTop: "1px solid rgba(0,0,0,0.06)", padding: "18px 28px 22px" } },
                // File indicator
                uploadingFiles && React.createElement("div", {
                    style: { maxWidth: 900, margin: "0 auto 8px", display: "flex", alignItems: "center", gap: 8, background: "#FFF7ED", border: "1px solid #FDD8A5", borderRadius: 10, padding: "8px 14px" }
                },
                    React.createElement("span", { style: { fontSize: 16 } }, "⏳"),
                    React.createElement("div", { style: { fontSize: 12, color: "#9A5A00", fontWeight: 600 } }, uploadProgressText || "A processar anexos...")
                ),
                // File indicator
                activeUploadedFiles.length > 0 && active && active.fileMode && React.createElement("div", {
                    style: { maxWidth: 900, margin: "0 auto 8px", display: "flex", alignItems: "flex-start", gap: 8, background: "#FFF5F5", border: "1px solid #FFE0E0", borderRadius: 10, padding: "8px 14px" }
                },
                    React.createElement("span", { style: { fontSize: 16, marginTop: 2 } }, "📊"),
                    React.createElement("div", { style: { flex: 1 } },
                        React.createElement("div", { style: { fontSize: 12, fontWeight: 600, color: "#DE3163", marginBottom: 4 } }, `${activeUploadedFiles.length}/${maxFilesPerConversation} ficheiro(s) anexado(s)`),
                        React.createElement("div", { style: { fontSize: 11, color: "#888", display: "flex", flexDirection: "column", gap: 2 } },
                            activeUploadedFiles.slice(-5).map((f, idx) =>
                                React.createElement("div", { key: `uf-${idx}` },
                                    `• ${f.filename} (${f.rows || 0} linhas${Array.isArray(f.columns) ? ` · ${f.columns.length} colunas` : ""})`
                                )
                            ),
                            activeUploadedFiles.length > 5 && React.createElement("div", null, `... +${activeUploadedFiles.length - 5} ficheiro(s)`)
                        )
                    )
                ),
                // Image previews
                imagePreviews.length > 0 && React.createElement("div", {
                    style: { maxWidth: 900, margin: "0 auto 8px", display: "flex", gap: 8, flexWrap: "wrap", background: "#FFF5F7", border: "1px solid #FFE0E8", borderRadius: 10, padding: "8px 14px" }
                },
                    imagePreviews.map((img, idx) =>
                        React.createElement("div", { key: idx, style: { position: "relative", display: "inline-flex", alignItems: "center", gap: 6 } },
                            React.createElement("img", { src: img.dataUrl, style: { width: 44, height: 44, borderRadius: 8, objectFit: "cover" } }),
                            React.createElement("div", { style: { maxWidth: 100 } },
                                React.createElement("div", { style: { fontSize: 11, fontWeight: 600, color: "#DE3163", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" } }, img.filename),
                                React.createElement("div", { style: { fontSize: 10, color: "#888" } }, img.size)
                            ),
                            React.createElement("button", {
                                onClick: () => removeImage(idx),
                                style: { position: "absolute", top: -4, right: -4, background: "#DE3163", color: "white", border: "none", borderRadius: "50%", width: 18, height: 18, fontSize: 11, cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center" }
                            }, "✕")
                        )
                    ),
                    React.createElement("button", { onClick: () => setImagePreviews([]), style: { marginLeft: "auto", background: "none", border: "none", color: "#999", cursor: "pointer", fontSize: 12, alignSelf: "center" } }, "Limpar"),
                    React.createElement("div", { style: { alignSelf: "center", fontSize: 11, color: "#888" } }, `${imagePreviews.length}/${maxImagesPerMessage}`)
                ),
                modelTier === "fast" && (showFastAnalyticHint || tierRoutingNotice) && React.createElement("div", {
                    style: {
                        maxWidth: 900, margin: "0 auto 8px", display: "flex", alignItems: "flex-start", gap: 8,
                        background: "rgba(222,49,99,0.06)", border: "1px solid rgba(222,49,99,0.16)", borderRadius: 10, padding: "8px 14px",
                    }
                },
                    React.createElement("span", { style: { fontSize: 14, marginTop: 1 } }, "⚠️"),
                    React.createElement("div", {
                        style: { fontSize: 12, color: "#8a1f3e", fontWeight: 500 }
                    },
                        tierRoutingNotice || "No modo Fast, pedidos analíticos podem perder qualidade. Ao enviar, o sistema encaminha automaticamente para Thinking."
                    )
                ),
                React.createElement("div", { style: { maxWidth: 900, margin: "0 auto", display: "flex", gap: 8, alignItems: "flex-end" } },
                    React.createElement("input", { ref: fileInputRef, type: "file", accept: ".xlsx,.xls,.csv,.txt,.pdf,.svg,.png,.jpg,.jpeg,.gif,.webp,.bmp,.pptx", multiple: true, style: { display: "none" }, onChange: handleFileUpload }),
                    React.createElement("input", { ref: imageInputRef, type: "file", accept: "image/jpeg,image/png,image/gif,image/webp", multiple: true, style: { display: "none" }, onChange: handleImageUpload }),
                    // File button
                    React.createElement("button", {
                        onClick: () => fileInputRef.current && fileInputRef.current.click(), disabled: loading || uploadingFiles, title: "Carregar ficheiro",
                        style: {
                            width: 44, height: 44, borderRadius: 12,
                            background: "transparent", color: "#bbb",
                            border: "1.5px solid rgba(0,0,0,0.08)",
                            cursor: "pointer", display: "flex", alignItems: "center",
                            justifyContent: "center", flexShrink: 0,
                            transition: "all 0.2s ease", fontSize: 17,
                        },
                        onMouseEnter: e => { e.currentTarget.style.borderColor = "#DE3163"; e.currentTarget.style.color = "#DE3163"; e.currentTarget.style.background = "rgba(222,49,99,0.04)"; },
                        onMouseLeave: e => { e.currentTarget.style.borderColor = "rgba(0,0,0,0.08)"; e.currentTarget.style.color = "#bbb"; e.currentTarget.style.background = "transparent"; },
                    }, "📎"),
                    // Image button
                    React.createElement("button", {
                        onClick: () => imageInputRef.current && imageInputRef.current.click(), disabled: loading, title: "Anexar imagens",
                        style: {
                            width: 44, height: 44, borderRadius: 12,
                            background: imagePreviews.length > 0 ? "rgba(222,49,99,0.06)" : "transparent",
                            color: imagePreviews.length > 0 ? "#DE3163" : "#bbb",
                            border: imagePreviews.length > 0 ? "1.5px solid rgba(222,49,99,0.3)" : "1.5px solid rgba(0,0,0,0.08)",
                            cursor: "pointer", display: "flex", alignItems: "center",
                            justifyContent: "center", flexShrink: 0,
                            transition: "all 0.2s ease", fontSize: 17,
                        },
                    }, "🖼️"),
                    // Text input
                    React.createElement("textarea", {
                        ref: inputRef, value: input, onChange: e => setInput(e.target.value),
                        onKeyDown: handleKeyDown, onPaste: handlePaste,
                        placeholder: activeUploadedFiles.length > 0 && active && active.fileMode
                            ? `Pergunta sobre os ${activeUploadedFiles.length} ficheiros anexados...`
                            : agentMode === "userstory" ? "Descreve a funcionalidade para gerar User Stories..." : "Faz uma pergunta sobre DevOps, KPIs, User Stories...",
                        rows: 1,
                        style: {
                            flex: 1, resize: "none",
                            border: "1.5px solid rgba(0,0,0,0.08)",
                            borderRadius: 14, padding: "12px 20px",
                            fontSize: 14, fontFamily: "'Montserrat', sans-serif",
                            lineHeight: 1.5, background: "#FAFAF8", color: "#1a1a1a",
                            transition: "all 0.2s ease",
                            minHeight: 46, maxHeight: 120, outline: "none",
                        },
                        onFocus: e => { e.target.style.borderColor = "#DE3163"; e.target.style.background = "white"; e.target.style.boxShadow = "0 0 0 3px rgba(222,49,99,0.06)"; },
                        onBlur: e => { e.target.style.borderColor = "rgba(0,0,0,0.08)"; e.target.style.background = "#FAFAF8"; e.target.style.boxShadow = "none"; },
                        onInput: e => { e.target.style.height = "auto"; e.target.style.height = Math.min(e.target.scrollHeight, 120) + "px"; }
                    }),
                    // Send button
                    React.createElement("button", {
                        className: "send-btn", onClick: send, disabled: !input.trim() || loading || uploadingFiles,
                        style: {
                            width: 44, height: 44, borderRadius: 12,
                            background: "#1A1A1A", color: "white",
                            border: "none", cursor: "pointer",
                            display: "flex", alignItems: "center", justifyContent: "center",
                            flexShrink: 0, transition: "all 0.2s ease", fontSize: 16,
                        }
                    }, "➤")
                ),
                React.createElement("div", {
                    style: {
                        maxWidth: 900, margin: "8px auto 0",
                        textAlign: "center", fontSize: 10, color: "#ccc",
                        fontWeight: 400, letterSpacing: "0.2px",
                    }
                }, uploadingFiles
                    ? "A processar anexos. O envio da mensagem fica disponível no fim."
                    : `Enter para enviar · Shift+Enter nova linha · 📎 ficheiros · Ctrl+V colar imagens · lote até ${Math.max(1, Math.round(maxBatchTotalBytes / (1024 * 1024)))}MB`)
            )
        )
    );
}

export default App;
