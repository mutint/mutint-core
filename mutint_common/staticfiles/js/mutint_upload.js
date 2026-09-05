/* Chunked upload to mutint-core's staging area.
 *
 * This was inline in `import/add.html` and had exactly one caller, which is a fine place for
 * it to live until there are two. `mutint-breseq` is the second: it stages FASTQ reads for a
 * breseq run rather than files for the import registry, and the difference between the two is
 * one field in one request -- everything else here, the manifest, the 8 MB slices, the resume
 * on a 409 and the retry budget, is the same code or it is two codes that will drift.
 *
 * Loaded from base.html beside mutint_crud.js, so a page gets it with nothing to include.
 *
 * Usage:
 *
 *     mutintUpload(entries, {
 *         experimentId: 4,
 *         importType: "breseq_folder",   // xor consumer:
 *         consumer: "mutint_breseq",     // an installed app label
 *         onProgress: function (done, total, label) { ... }
 *     }).then(function (uploadId) { ... });
 *
 * `entries` is `[{file: File, path: "sample/data/output.gd"}, ...]`. The path is relative and
 * untrusted; the server sanitizes it and confirms every write lands inside the session's own
 * directory. `mutintCollectDropped(dataTransfer)` builds that list from a drop, following
 * directories, which is what lets a breseq folder arrive with its shape intact.
 *
 * The promise resolves with the upload id. What happens next differs by caller -- core's Add
 * page finalizes through the import registry, a component posts to its own endpoint -- and is
 * deliberately not this file's business.
 */
(function () {
    "use strict";

    var CHUNK_BYTES = 8 * 1024 * 1024;
    var MAX_CHUNK_RETRIES = 3;

    // XHR rather than fetch: fetch reports no upload progress at all.
    function postChunk(uploadId, path, offset, blob, onProgress) {
        return new Promise(function (resolve, reject) {
            var form = new FormData();
            form.append("path", path);
            form.append("offset", String(offset));
            form.append("chunk", blob, "chunk");

            var xhr = new XMLHttpRequest();
            xhr.open("POST", "/import/uploads/" + uploadId + "/chunk");
            xhr.setRequestHeader("X-CSRFToken", window.mutintCsrfHeader());
            if (onProgress) {
                xhr.upload.onprogress = function (e) {
                    if (e.lengthComputable) { onProgress(e.loaded); }
                };
            }
            xhr.onload = function () {
                var body = {};
                try { body = JSON.parse(xhr.responseText || "{}"); } catch (err) { body = {}; }
                if (xhr.status >= 200 && xhr.status < 300) { resolve(body); }
                else {
                    var error = new Error(body.error || ("HTTP " + xhr.status));
                    error.status = xhr.status;
                    error.expectedOffset = body.expected_offset;
                    reject(error);
                }
            };
            xhr.onerror = function () { reject(new Error("Network error")); };
            xhr.send(form);
        });
    }

    function uploadAll(uploadId, entries, onProgress) {
        var totalBytes = entries.reduce(function (s, e) { return s + e.file.size; }, 0);
        var doneBytes = 0;

        function report(done, label) {
            if (onProgress) { onProgress(done, totalBytes, label); }
        }

        return entries.reduce(function (chain, entry) {
            return chain.then(function () {
                var offset = 0;
                var settled = doneBytes;

                function nextChunk(attempt) {
                    if (offset >= entry.file.size) {
                        doneBytes = settled + entry.file.size;
                        return Promise.resolve();
                    }
                    var end = Math.min(offset + CHUNK_BYTES, entry.file.size);
                    var blob = entry.file.slice(offset, end);
                    var base = offset;
                    return postChunk(uploadId, entry.path, offset, blob, function (loaded) {
                        report(settled + base + loaded, "Uploading " + entry.path);
                    }).then(function () {
                        offset = end;
                        doneBytes = settled + offset;
                        report(doneBytes, "Uploading " + entry.path);
                        return nextChunk(0);
                    }).catch(function (err) {
                        // The server says where it really got to; believe it rather than
                        // retrying from an offset it has already rejected.
                        if (err.status === 409 && typeof err.expectedOffset === "number") {
                            offset = err.expectedOffset;
                            return nextChunk(0);
                        }
                        if (attempt < MAX_CHUNK_RETRIES) { return nextChunk(attempt + 1); }
                        throw new Error(entry.path + ": " + err.message);
                    });
                }
                return nextChunk(0);
            });
        }, Promise.resolve());
    }

    /* Open a session, upload every entry into it, resolve with the upload id. */
    window.mutintUpload = function (entries, options) {
        options = options || {};
        var manifest = entries.map(function (entry) {
            return { path: entry.path, size: entry.file.size };
        });

        var url, body;
        if (options.consumer) {
            url = "/import/staging/";
            body = { experiment_id: options.experimentId,
                     consumer: options.consumer, files: manifest };
        } else {
            url = "/import/uploads/";
            body = { experiment_id: options.experimentId,
                     import_type: options.importType, files: manifest };
        }

        var uploadId = null;
        return window.mutintPostJson(url, body).then(function (session) {
            uploadId = session.upload_id;
            return uploadAll(uploadId, entries, options.onProgress);
        }).then(function () {
            return uploadId;
        });
    };

    /* `[{file, path}]` from a drop, descending into directories.
     *
     * entry.fullPath is what lets the server reconstruct sample folders; without it a breseq
     * drop arrives as a heap of files named output.gd, reference.bam and so on, several of
     * them identically. Falls back to the flat file list where the entries API is absent.
     */
    window.mutintCollectDropped = function (dataTransfer) {
        function readEntry(entry, collected) {
            return new Promise(function (resolve) {
                if (entry.isFile) {
                    entry.file(function (file) {
                        collected.push({
                            file: file,
                            path: (entry.fullPath || file.name).replace(/^\//, "")
                        });
                        resolve();
                    }, function () { resolve(); });
                } else if (entry.isDirectory) {
                    var reader = entry.createReader();
                    var readBatch = function () {
                        // readEntries answers in batches and signals the end with an empty
                        // one; a single call sees only the first hundred or so.
                        reader.readEntries(function (entries) {
                            if (!entries.length) { resolve(); return; }
                            Promise.all(entries.map(function (en) {
                                return readEntry(en, collected);
                            })).then(readBatch);
                        }, function () { resolve(); });
                    };
                    readBatch();
                } else { resolve(); }
            });
        }

        var items = dataTransfer.items;
        if (items && items.length && items[0].webkitGetAsEntry) {
            var collected = [];
            var promises = [];
            for (var i = 0; i < items.length; i++) {
                var entry = items[i].webkitGetAsEntry();
                if (entry) { promises.push(readEntry(entry, collected)); }
            }
            return Promise.all(promises).then(function () { return collected; });
        }
        return Promise.resolve(window.mutintFromFileList(dataTransfer.files));
    };

    /* `[{file, path}]` from an <input type=file>, honouring webkitRelativePath. */
    window.mutintFromFileList = function (fileList) {
        return Array.prototype.slice.call(fileList).map(function (file) {
            return { file: file, path: file.webkitRelativePath || file.name };
        });
    };
}());
