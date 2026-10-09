function main() {
    "use strict";

    // Configuration flags - Edit these to change behavior
    const DEFAULT_PORT = 20021;       // Preferred port; the server probes upward from here.
    const MAX_PORT_ATTEMPTS = 100;    // Probe DEFAULT_PORT .. DEFAULT_PORT + MAX_PORT_ATTEMPTS - 1.

    // Create TCP listener
    const server = network.createListener();

    server.on("connection", conn => {
        let buffer = "";

        // Handle incoming data on this connection.
        conn.on("data", data => {
            buffer += data;
            // Split messages on newline; we assume one JSON blob per line.
            const lines = buffer.split("\n");
            // If the last element is not empty, it means the last line is incomplete.
            if (lines[lines.length - 1] !== "") {
                buffer = lines.pop();
            } else {
                // All lines complete; clear the buffer.
                buffer = "";
                // Remove the empty string after the trailing newline.
                lines.pop();
            }

            // Process each complete JSON message.
            for (const line of lines) {
                let request;
                try {
                    request = JSON.parse(line);
                } catch (e) {
                    conn.write(JSON.stringify({
                        success: false,
                        error: "Invalid JSON"
                    }) + "\n");
                    continue;
                }

                processRequest(request, response => {
                    // Send the response as a JSON blob followed by a newline.
                    conn.write(JSON.stringify(response) + "\n");
                });
            }
        });
    });

    // Bind to the first available port at or above DEFAULT_PORT. This lets
    // several OpenRCT2 instances run concurrently — each grabs the next free
    // port (8080, 8081, 8082, ...). server.listen() throws synchronously when
    // the port is already in use (the native socket's bind() fails with
    // EADDRINUSE), so we catch and advance to the next candidate.
    let port = null;
    for (let candidate = DEFAULT_PORT; candidate < DEFAULT_PORT + MAX_PORT_ATTEMPTS; candidate++) {
        try {
            server.listen(candidate);
            port = candidate;
            break;
        } catch (e) {
            console.log(`Port ${candidate} unavailable (${e}); trying next port.`);
        }
    }

    if (port === null) {
        console.log(`Ride API server failed to bind a port in range ${DEFAULT_PORT}-${DEFAULT_PORT + MAX_PORT_ATTEMPTS - 1}.`);
        return;
    }
    console.log(`Ride API server listening on port ${port}.`);

    // Promise-wrapped context.executeAction. Rejects on result.error so
    // failed actions surface as exceptions in async handlers.
    function executeAction(action, args) {
        return new Promise((resolve, reject) => {
            context.executeAction(action, args, result => {
                if (!result || (result.error && result.error !== "")) {
                    reject(new Error((result && result.error) || "Unknown error"));
                } else {
                    resolve(result);
                }
            });
        });
    }

    // Run an async handler and convert its resolved value / thrown error
    // into the standard {success, payload|error} response shape.
    function runHandler(handlerPromise, callback) {
        handlerPromise
            .then(payload => callback({ success: true, payload }))
            .catch(e => callback({ success: false, error: e && e.message ? e.message : String(e) }));
    }

    // Track state storage (ride ID -> RideState)
    const rideTrackStates = new Map();

    function serializeTrackSegment(s) {
        return {
            type: s.type,
            description: s.description,
            trackGroup: s.trackGroup,
            length: s.length,
            beginZ: s.beginZ,
            endZ: s.endZ,
            endX: s.endX,
            endY: s.endY,
            beginSlope: s.beginSlope,
            endSlope: s.endSlope,
            beginBank: s.beginBank,
            endBank: s.endBank,
            beginDirection: s.beginDirection,
            endDirection: s.endDirection,
            turnDirection: s.turnDirection,
            slopeDirection: s.slopeDirection,
            elements: (s.elements || []).map(e => ({ x: e.x, y: e.y, z: e.z })),
        };
    }

    const endpoints = new Map([
        // mode is "normal" with a park loaded, "title" on the title screen (whose demo
        // park still answers every query), or an editor mode.
        ["health",               () => Promise.resolve({ status: "ok", plugin: "ride-builder", port,
                                     mode: context.mode, park: context.mode === "normal" ? park.name : null })],
        ["listAllRides",         () => handleListAllRides()],
        ["getAllTrackSegments",  () => handleGetAllTrackSegments()],
        ["deleteAllRides",       () => handleDeleteAllRides()],
        ["deleteRide",           params => handleDeleteRide(params)],
        ["startRideTest",        params => handleStartRideTest(params)],
        ["testRide",             params => handleStartRideTest(params)],
        ["getRideStats",         params => handleGetRideStats(params)],
        ["getRideMaintenance",   params => handleGetRideMaintenance(params)],
        ["getRideTrains",        params => handleGetRideTrains(params)],
        ["moveCamera",           params => handleMoveCamera(params)],
        ["queryActions",         params => handleQueryActions(params)],
        ["getMapSnapshot",       params => handleGetMapSnapshot(params)],
        ["getMapChanges",        params => handleGetMapChanges(params)],
        ["getRecentActions",     params => handleGetRecentActions(params)],
        ["loadObject",           params => handleLoadObject(params)],
        ["findInstalledObjects", params => handleFindInstalledObjects(params)],
        ["listRideMaintenance",  () => handleListRideMaintenance()],
        ["getGameSpeed",         () => handleGetGameSpeed()],
        ["getElementsInRect",    params => handleGetElementsInRect(params)],
        ["getGuestsInRect",      params => handleGetGuestsInRect(params)],
        ["placeTrackPiece",      params => handlePlaceTrackPiece(params)],
        ["getValidNextPieces",   params => handleGetValidNextPieces(params)],
        ["placeEntranceExit",    params => handlePlaceEntranceExit(params)],
        ["deleteLastTrackPiece", params => handleDeleteLastTrackPiece(params)],
        ["undoLastPiece",        params => handleDeleteLastTrackPiece(params)],
        ["createRide",           params => handleCreateRide(params)],
        ["listLoadedRideObjects", () => handleListLoadedRideObjects()],
        ["listLoadedScenery",    params => handleListLoadedScenery(params)],
        ["probePlacePiece",      params => handleProbePlacePiece(params)],
        ["previewTrackPiece",    params => handlePreviewTrackPiece(params)],
        ["exportRideDesign",     params => handleExportRideDesign(params)],
        ["placeRideDesign",      params => handlePlaceRideDesign(params)],
        ["probeRideDesign",      params => handleProbeRideDesign(params)],
    ]);

    /**
     * Dispatches a JSON request to its endpoint handler.
     * Looks up endpoint by name in the `endpoints` Map and calls
     * the matching async handler. Resolved values become
     * {success: true, payload}; thrown errors become {success: false, error}.
     */
    function processRequest(request, callback) {
        if (!request.endpoint) {
            callback({ success: false, error: "Missing endpoint" });
            return;
        }
        const handler = endpoints.get(request.endpoint);
        if (!handler) {
            callback({ success: false, error: `Unknown endpoint: ${request.endpoint}` });
            return;
        }
        runHandler(handler(request.params), callback);
    }

    async function handleListAllRides() {
        const ridesArray = [];
        map.rides.forEach(ride => {
            ridesArray.push({ id: ride.id, name: ride.name, type: ride.type });
        });
        return ridesArray;
    }

    async function handleListLoadedRideObjects() {
        const all = objectManager.getAllObjects("ride");
        return all.map(o => ({
            index: o.index,
            identifier: o.identifier,
            name: o.name,
            rideType: o.rideType,
        }));
    }

    async function handleListLoadedScenery(params) {
        const kind = (params && params.kind) || "small_scenery";
        const all = objectManager.getAllObjects(kind);
        return all.map(o => {
            const row = { index: o.index, identifier: o.identifier, name: o.name };
            if (kind === "small_scenery" && typeof o.flags === "number") {
                // SMALL_SCENERY_FLAG_FULL_TILE (bit 0): fills the tile; otherwise it sits in a quarter.
                row.fullTile = (o.flags & 1) !== 0;
                row.height = o.height;
                row.price = o.price;
            }
            if (o.sceneryGroup !== undefined) row.sceneryGroup = o.sceneryGroup;
            return row;
        });
    }

    async function handleGetAllTrackSegments() {
        return context.getAllTrackSegments().map(serializeTrackSegment);
    }

    function readReliabilityPercent(ride) {
        const keys = ["reliability", "reliabilityPercentage", "reliability_percentage"];
        for (let i = 0; i < keys.length; i++) {
            const value = ride[keys[i]];
            if (typeof value === "number" && Number.isFinite(value)) {
                return value <= 100 ? value : Math.round(value / 655.35);
            }
        }
        return null;
    }

    function stationQueueMetrics(ride) {
        const stations = ride.stations || [];
        const stationQueueTimes = [];
        let queueTime = null;
        for (let i = 0; i < stations.length; i++) {
            const st = stations[i];
            // ride.stations lists every station slot (255), so skip unused ones.
            if (!st || !st.start || typeof st.queueTime !== "number") continue;
            stationQueueTimes.push({ index: i, queueTime: st.queueTime });
            queueTime = queueTime == null ? st.queueTime : Math.max(queueTime, st.queueTime);
        }
        return { queueTime, stationQueueTimes };
    }

    function serializeRideMaintenance(ride) {
        const breakdown = ride.breakdown;
        const breakdownLabel = breakdown == null ? "none" : String(breakdown).toLowerCase();
        const guestCount = typeof ride.guestCount === "number" ? ride.guestCount : null;
        const queueMetrics = stationQueueMetrics(ride);
        return {
            rideId: ride.id,
            name: ride.name,
            classification: ride.classification,
            status: ride.status,
            downtime: ride.downtime,
            reliability: readReliabilityPercent(ride),
            age: ride.age,
            breakdown: breakdown,
            activeBreakdown: breakdownLabel !== "none" && breakdownLabel !== "" && breakdownLabel !== "0",
            satisfaction: ride.satisfaction,
            inspectionInterval: ride.inspectionInterval,
            guestCount: guestCount,
            isEmpty: typeof ride.isEmpty === "boolean"
                ? ride.isEmpty
                : (typeof guestCount === "number" ? guestCount === 0 : null),
            incomePerHour: typeof ride.incomePerHour === "number" ? ride.incomePerHour : null,
            profit: typeof ride.profit === "number" ? ride.profit : null,
            queueTime: queueMetrics.queueTime,
            stationQueueTimes: queueMetrics.stationQueueTimes,
        };
    }

    async function handleGetGameSpeed() {
        return {
            apiVersion: context.apiVersion,
            gameSpeed: typeof context.gameSpeed === "number" ? context.gameSpeed : null,
        };
    }

    // Match mcp-server map_region.MAX_REGION_SIDE — clamp oversized socket clients.
    const MAX_RECT_SIDE = 40;

    function isFiniteInteger(n) {
        return typeof n === "number" && Number.isFinite(n) && Number.isInteger(n);
    }

    function normalizeRectBounds(bounds) {
        if (!bounds || typeof bounds !== "object") return null;
        if (
            !isFiniteInteger(bounds.minX) ||
            !isFiniteInteger(bounds.maxX) ||
            !isFiniteInteger(bounds.minY) ||
            !isFiniteInteger(bounds.maxY)
        ) {
            return null;
        }
        let minX = Math.min(bounds.minX, bounds.maxX);
        let maxX = Math.max(bounds.minX, bounds.maxX);
        let minY = Math.min(bounds.minY, bounds.maxY);
        let maxY = Math.max(bounds.minY, bounds.maxY);
        const width = maxX - minX + 1;
        const height = maxY - minY + 1;
        if (width > MAX_RECT_SIDE) {
            maxX = minX + MAX_RECT_SIDE - 1;
        }
        if (height > MAX_RECT_SIDE) {
            maxY = minY + MAX_RECT_SIDE - 1;
        }
        return { minX, maxX, minY, maxY };
    }

    function summarizeFootpathElement(tileX, tileY, el) {
        const summary = {
            tileX,
            tileY,
            baseZ: el.baseZ,
            isQueue: !!el.isQueue,
        };
        if (typeof el.additionStatus === "number") summary.additionStatus = el.additionStatus;
        if (typeof el.isAdditionBroken === "boolean") summary.isAdditionBroken = el.isAdditionBroken;
        if (typeof el.isAdditionFull === "boolean") summary.isAdditionFull = el.isAdditionFull;
        return summary;
    }

    function summarizeTrackElement(tileX, tileY, el) {
        return {
            tileX,
            tileY,
            baseZ: el.baseZ,
            trackType: el.trackType,
            ride: el.ride,
            sequenceIndex: el.sequenceIndex != null ? el.sequenceIndex : el.sequence,
        };
    }

    function summarizeEntranceElement(tileX, tileY, el) {
        // EntranceElement.object is ENTRANCE_TYPE: 0=ride entrance, 1=ride exit, 2=park entrance.
        // Native summaries may already expose isExit.
        const isExit = typeof el.isExit === "boolean" ? el.isExit : el.object === 1;
        return {
            tileX,
            tileY,
            baseZ: el.baseZ,
            ride: el.ride,
            station: el.station,
            isExit,
        };
    }

    function scanElementsInRect(type, bounds) {
        const out = [];
        for (let ty = bounds.minY; ty <= bounds.maxY; ty++) {
            for (let tx = bounds.minX; tx <= bounds.maxX; tx++) {
                const tile = map.getTile(tx, ty);
                if (!tile) continue;
                const elements = tile.elements || [];
                for (let i = 0; i < elements.length; i++) {
                    const el = elements[i];
                    if (!el || el.type !== type) continue;
                    if (type === "footpath") out.push(summarizeFootpathElement(tx, ty, el));
                    else if (type === "track") out.push(summarizeTrackElement(tx, ty, el));
                    else if (type === "entrance") out.push(summarizeEntranceElement(tx, ty, el));
                }
            }
        }
        return out;
    }

    function serializeGuestNearTile(guest) {
        return {
            id: guest.id,
            name: guest.name,
            happiness: guest.happiness,
            tile: [Math.floor(guest.x / 32), Math.floor(guest.y / 32)],
            // Native thought objects serialize to {}, so copy their fields.
            thoughts: (guest.thoughts || []).slice(0, 5).map(t => ({
                type: t.type, item: t.item, freshness: t.freshness, freshTimeout: t.freshTimeout,
            })),
        };
    }

    function scanGuestsInRect(bounds) {
        // Prefer per-tile query when available (OpenRCT2 develop).
        if (typeof map.getAllEntitiesOnTile === "function") {
            const out = [];
            const seen = {};
            for (let ty = bounds.minY; ty <= bounds.maxY; ty++) {
                for (let tx = bounds.minX; tx <= bounds.maxX; tx++) {
                    // CoordsXY is world units (32 per tile), same as getTrackIterator.
                    const guests = map.getAllEntitiesOnTile("guest", { x: tx * 32, y: ty * 32 }) || [];
                    for (let i = 0; i < guests.length; i++) {
                        const guest = guests[i];
                        if (!guest || seen[guest.id]) continue;
                        seen[guest.id] = true;
                        out.push(serializeGuestNearTile(guest));
                    }
                }
            }
            return out;
        }
        const guests = map.getAllEntities("guest") || [];
        return guests
            .filter(guest => {
                const tx = Math.floor(guest.x / 32);
                const ty = Math.floor(guest.y / 32);
                return tx >= bounds.minX && tx <= bounds.maxX && ty >= bounds.minY && ty <= bounds.maxY;
            })
            .map(serializeGuestNearTile);
    }

    function summarizeRectElement(type, el) {
        const tileX = typeof el.tileX === "number" ? el.tileX : null;
        const tileY = typeof el.tileY === "number" ? el.tileY : null;
        if (tileX == null || tileY == null) return null;
        if (type === "footpath") return summarizeFootpathElement(tileX, tileY, el);
        if (type === "track") return summarizeTrackElement(tileX, tileY, el);
        if (type === "entrance") return summarizeEntranceElement(tileX, tileY, el);
        return null;
    }

    async function handleGetElementsInRect(params) {
        const { type, bounds: rawBounds } = params || {};
        if (typeof type !== "string" || !rawBounds) {
            throw new Error("Missing or invalid parameters: type, bounds");
        }
        if (type !== "footpath" && type !== "track" && type !== "entrance") {
            throw new Error("type must be footpath, track, or entrance");
        }
        const bounds = normalizeRectBounds(rawBounds);
        if (!bounds) throw new Error("Invalid bounds");
        // Native map.getElementsInRect was proposed in #26675 but not merged; keep
        // a compatible endpoint via tile scan (and prefer native if it lands later).
        if (typeof map.getElementsInRect === "function") {
            const elements = map.getElementsInRect(type, bounds) || [];
            return elements.map(el => summarizeRectElement(type, el)).filter(Boolean);
        }
        return scanElementsInRect(type, bounds);
    }

    async function handleGetGuestsInRect(params) {
        const { bounds: rawBounds } = params || {};
        if (!rawBounds) {
            throw new Error("Missing parameter: bounds");
        }
        const bounds = normalizeRectBounds(rawBounds);
        if (!bounds) throw new Error("Invalid bounds");
        if (typeof map.getGuestsInRect === "function") {
            return map.getGuestsInRect(bounds).map(serializeGuestNearTile);
        }
        return scanGuestsInRect(bounds);
    }

    async function handleGetRideStats(params) {
        const { rideId } = params || {};
        if (typeof rideId !== "number") throw new Error("Missing or invalid parameter: rideId");
        const ride = map.getRide(rideId);
        if (!ride) throw new Error("Ride not found");
        return {
            excitement: ride.excitement / 100,
            intensity: ride.intensity / 100,
            nausea: ride.nausea / 100,
        };
    }

    // Centre the main view on a tile (optionally set zoom 0-5 and rotation 0-3) so
    // screenshots show what an agent just built. Only works with a UI (not headless).
    async function handleMoveCamera(params) {
        if (typeof ui === "undefined" || !ui.mainViewport) throw new Error("No UI viewport (headless game)");
        const { x, y, zoom, rotation } = params || {};
        if (typeof x !== "number" || typeof y !== "number") throw new Error("Missing or invalid parameters: x, y (tiles)");
        const vp = ui.mainViewport;
        if (typeof rotation === "number") vp.rotation = rotation & 3;
        if (typeof zoom === "number") vp.zoom = Math.max(0, Math.min(5, zoom));
        const surface = map.getTile(x, y).elements.find(e => e.type === "surface");
        vp.moveTo({ x: x * 32 + 16, y: y * 32 + 16, z: surface ? surface.baseZ : 0 });
        return { x, y, zoom: vp.zoom, rotation: vp.rotation };
    }

    // Price a batch of game actions without running them (context.queryAction):
    // one entry per args object with cost (money units) and any error.
    async function handleQueryActions(params) {
        const { action, argsList } = params || {};
        if (typeof action !== "string" || !Array.isArray(argsList)) throw new Error("Missing or invalid parameters: action, argsList");
        if (typeof context.queryAction !== "function") throw new Error("queryAction not available");
        const results = [];
        for (const args of argsList) {
            results.push(await new Promise(resolve => context.queryAction(action, args, r => resolve({
                cost: r ? r.cost : null,
                error: r ? r.error : -1,
                errorTitle: r ? r.errorTitle : undefined,
                errorMessage: r ? r.errorMessage : undefined,
            }))));
        }
        return results;
    }

    // ---- Map snapshot and change tracking ------------------------------------
    // Heights are tile_z (baseZ / 8) throughout. Dense layers are row-major,
    // lowest y first: index = (y - minY) * width + (x - minX).
    const SNAPSHOT_MAX_SIDE = 64;
    const FLAG = {
        OWNED: 1, CONSTRUCTION_RIGHTS: 2, WATER: 4, PATH: 8, QUEUE: 16, TRACK: 32,
        ENTRANCE: 64, SMALL_SCENERY: 128, LARGE_SCENERY: 256, WALL: 512, BANNER: 1024,
    };
    const SCENERY_KIND = { small_scenery: 0, large_scenery: 1, wall: 2, banner: 3 };
    const tz = z => Math.floor(z / 8);

    async function handleGetMapSnapshot(params) {
        const b = params && params.bounds;
        if (!b || !isFiniteInteger(b.minX) || !isFiniteInteger(b.minY) || !isFiniteInteger(b.maxX) || !isFiniteInteger(b.maxY)) {
            throw new Error("Missing or invalid parameter: bounds {minX, minY, maxX, maxY} (tiles)");
        }
        const minX = Math.max(0, Math.min(b.minX, b.maxX));
        const minY = Math.max(0, Math.min(b.minY, b.maxY));
        const maxX = Math.min(map.size.x - 1, Math.max(b.minX, b.maxX), minX + SNAPSHOT_MAX_SIDE - 1);
        const maxY = Math.min(map.size.y - 1, Math.max(b.minY, b.maxY), minY + SNAPSHOT_MAX_SIDE - 1);
        const width = Math.max(0, maxX - minX + 1);
        const height = Math.max(0, maxY - minY + 1);
        const n = width * height;
        const ground = new Array(n).fill(0), slope = new Array(n).fill(0), flags = new Array(n).fill(0);
        const water = new Array(n).fill(0), top = new Array(n).fill(0), style = new Array(n).fill(0);
        const paths = [], track = [], entrances = [], scenery = [];
        for (let y = minY; y <= maxY; y++) {
            for (let x = minX; x <= maxX; x++) {
                const i = (y - minY) * width + (x - minX);
                const tile = map.getTile(x, y);
                if (!tile) continue;
                let f = 0, t = 0;
                for (const el of tile.elements) {
                    if (el.isGhost) continue;
                    const z = tz(el.baseZ);
                    if (el.type === "surface") {
                        ground[i] = z;
                        slope[i] = el.slope || 0;
                        style[i] = el.surfaceStyle || 0;
                        if (el.hasOwnership) f |= FLAG.OWNED;
                        if (el.hasConstructionRights) f |= FLAG.CONSTRUCTION_RIGHTS;
                        if (el.waterHeight > el.baseZ) { f |= FLAG.WATER; water[i] = tz(el.waterHeight); }
                        continue;
                    }
                    t = Math.max(t, tz(el.clearanceZ));
                    if (el.type === "footpath") {
                        f |= FLAG.PATH;
                        if (el.isQueue) f |= FLAG.QUEUE;
                        paths.push([i, z, el.edges || 0, el.slopeDirection == null ? -1 : el.slopeDirection,
                            el.isQueue ? 1 : 0, el.addition == null ? -1 : el.addition, el.isAdditionBroken ? 1 : 0]);
                    } else if (el.type === "track") {
                        f |= FLAG.TRACK;
                        track.push([i, z, el.ride, el.trackType, el.sequence == null ? 0 : el.sequence, tz(el.clearanceZ), el.direction,
                            el.rideType == null ? -1 : el.rideType, el.hasChainLift ? 1 : 0]);
                    } else if (el.type === "entrance") {
                        f |= FLAG.ENTRANCE;
                        entrances.push([i, z, el.ride == null ? -1 : el.ride, el.station == null ? -1 : el.station, el.object, el.direction]);
                    } else if (el.type in SCENERY_KIND) {
                        const kind = SCENERY_KIND[el.type];
                        f |= [FLAG.SMALL_SCENERY, FLAG.LARGE_SCENERY, FLAG.WALL, FLAG.BANNER][kind];
                        const extra = kind === 0 ? el.quadrant : el.direction;
                        scenery.push([i, kind, el.object, z, tz(el.clearanceZ), extra == null ? 0 : extra]);
                    }
                }
                flags[i] = f;
                top[i] = t;
            }
        }
        return {
            bounds: { minX, minY, maxX, maxY }, width, height, mapSize: { x: map.size.x, y: map.size.y },
            revision: mapRevision, sessionId: mapSessionId,
            ground, slope, flags, water, top, style, paths, track, entrances, scenery,
        };
    }

    // Every executed game action bumps the revision and records which tiles
    // it may have touched, so the server can refresh only those.
    const CHANGE_LOG_MAX = 4000;
    const RECENT_ACTIONS_MAX = 50;
    let mapSessionId = Math.floor(Math.random() * 1e9);
    let mapRevision = 0;
    let changeLog = [];          // { rev, kind: "rect" | "ride" | "all", ... }
    let recentActions = [];

    function recordChange(entry) {
        mapRevision++;
        entry.rev = mapRevision;
        changeLog.push(entry);
        if (changeLog.length > CHANGE_LOG_MAX) changeLog = changeLog.slice(-CHANGE_LOG_MAX);
    }

    // Actions that change nothing on the map.
    const NON_MAP_ACTIONS = new Set([
        "ridesetprice", "ridesetname", "ridesetsetting", "ridesetvehicle", "ridesetappearance", "ridesetcolourscheme",
        "ridesetstatus", "ridefreezerating", "parksetloan", "parksetresearchfunding", "parksetname", "parksetparameter",
        "parksetentrancefee", "parkmarketing", "staffhire", "stafffire", "staffsetname", "staffsetorders",
        "staffsetcostume", "staffsetcolour", "staffsetpatrolarea", "guestsetname", "guestsetflags", "peeppickup",
        "peepspawnplace", "pausetoggle", "gamesetspeed", "setcheat", "cheatset", "parksetdate", "scenariosetsetting",
        "networkmodifygroup", "playerkick", "playersetgroup", "balloonpress", "ridecreate",
    ]);

    function changeFromAction(action, args, result) {
        args = args || {};
        const toTile = v => Math.floor(v / 32);
        if (typeof args.x1 === "number" && typeof args.y1 === "number" && typeof args.x2 === "number" && typeof args.y2 === "number") {
            return { kind: "rect", minX: toTile(Math.min(args.x1, args.x2)) - 1, minY: toTile(Math.min(args.y1, args.y2)) - 1,
                maxX: toTile(Math.max(args.x1, args.x2)) + 1, maxY: toTile(Math.max(args.y1, args.y2)) + 1 };
        }
        const pos = (result && result.position && result.position.x >= 0) ? result.position
            : (typeof args.x === "number" && typeof args.y === "number" ? args : null);
        if (pos) {
            const x = toTile(pos.x), y = toTile(pos.y);
            // Track pieces and ride footprints span several tiles; give them a wider margin.
            const r = (action === "trackplace" || action === "trackremove" || action === "rideentranceexitremove") ? 4 : 1;
            const out = { kind: "rect", minX: x - r, minY: y - r, maxX: x + r, maxY: y + r };
            if (typeof args.ride === "number") out.ride = args.ride;
            return out;
        }
        if (typeof args.ride === "number") return { kind: "ride", ride: args.ride };
        return { kind: "all" };
    }

    context.subscribe("action.execute", e => {
        try {
            if (e.result && e.result.error) return;
            const action = String(e.action || "").toLowerCase();
            recentActions.push({ action, args: e.args, player: e.player, cost: e.result ? e.result.cost : null, tick: date.ticksElapsed });
            if (recentActions.length > RECENT_ACTIONS_MAX) recentActions.shift();
            if (NON_MAP_ACTIONS.has(action)) return;
            const change = changeFromAction(action, e.args, e.result);
            change.action = action;
            recordChange(change);
        } catch (err) {
            recordChange({ kind: "all", action: "error" });
        }
    });
    context.subscribe("map.changed", () => {
        mapSessionId = Math.floor(Math.random() * 1e9);
        mapRevision = 0;
        changeLog = [];
    });

    async function handleGetMapChanges(params) {
        const since = params && typeof params.sinceRevision === "number" ? params.sinceRevision : -1;
        const sessionId = params && params.sessionId;
        const reset = { reset: true, revision: mapRevision, sessionId: mapSessionId, changes: [] };
        if (sessionId !== mapSessionId || since < 0 || since > mapRevision) return reset;
        const changes = changeLog.filter(c => c.rev > since);
        if (since < mapRevision && (changes.length === 0 || changes[0].rev !== since + 1)) return reset;
        return { reset: false, revision: mapRevision, sessionId: mapSessionId, changes };
    }

    async function handleGetRecentActions(params) {
        const limit = params && typeof params.limit === "number" ? params.limit : 20;
        return recentActions.slice(-limit);
    }

    // Load an installed object (ride, stall, scenery...) into the running park, e.g.
    // a cash machine the scenario did not include. Returns its type and index.
    async function handleLoadObject(params) {
        const identifier = params && params.identifier;
        if (typeof identifier !== "string" || !identifier) throw new Error("Missing or invalid parameter: identifier");
        if (typeof objectManager === "undefined" || typeof objectManager.load !== "function") {
            throw new Error("objectManager.load is not available in this OpenRCT2 build");
        }
        const obj = objectManager.load(identifier);
        if (!obj) throw new Error("Could not load " + identifier + " (not installed, or no free slot)");
        return { identifier: obj.identifier, type: obj.type, index: obj.index, name: obj.name };
    }

    // Search installed (not necessarily loaded) objects by identifier or name.
    async function handleFindInstalledObjects(params) {
        const query = String((params && params.query) || "").toLowerCase();
        const type = params && params.type;
        const limit = (params && params.limit) || 30;
        if (typeof objectManager === "undefined") throw new Error("objectManager is not available");
        return objectManager.installedObjects
            .filter(o => (!type || o.type === type)
                && (!query || o.identifier.toLowerCase().indexOf(query) >= 0 || String(o.name).toLowerCase().indexOf(query) >= 0))
            .slice(0, limit)
            .map(o => ({ identifier: o.identifier, type: o.type, name: o.name }));
    }

    // Trains only exist while a ride is open or testing; a closed ride reports 0.
    async function handleGetRideTrains(params) {
        const { rideId } = params || {};
        if (typeof rideId !== "number") throw new Error("Missing or invalid parameter: rideId");
        const ride = map.getRide(rideId);
        if (!ride) throw new Error("Ride not found");
        const carsPerTrain = [];
        const heads = [];
        (ride.vehicles || []).forEach(headId => {
            if (headId === 65535 || headId == null) return;
            const head = map.getEntity(headId);
            if (head) {
                // Where each train is: tile, height (tile_z), speed and state, for stall diagnosis.
                heads.push({
                    tile: [Math.floor(head.x / 32), Math.floor(head.y / 32)], z: Math.floor(head.z / 8),
                    velocity: head.velocity, acceleration: head.acceleration, status: head.status,
                    trackType: head.trackLocation ? undefined : undefined,
                    trackLocation: head.trackLocation ? { x: Math.floor(head.trackLocation.x / 32), y: Math.floor(head.trackLocation.y / 32), z: Math.floor(head.trackLocation.z / 8), direction: head.trackLocation.direction } : null,
                    poweredMaxSpeed: head.poweredMaxSpeed,
                });
            }
            let cars = 0;
            let id = headId;
            while (id != null && cars < 64) {
                const car = map.getEntity(id);
                if (!car) break;
                cars++;
                id = car.nextCarOnTrain;
            }
            carsPerTrain.push(cars);
        });
        return {
            rideId,
            status: ride.status,
            mode: ride.mode,
            trains: carsPerTrain.length,
            carsPerTrain,
            heads,
        };
    }

    async function handleGetRideMaintenance(params) {
        const { rideId } = params || {};
        if (typeof rideId !== "number") throw new Error("Missing or invalid parameter: rideId");
        const ride = map.getRide(rideId);
        if (!ride) throw new Error("Ride not found");
        return serializeRideMaintenance(ride);
    }

    async function handleListRideMaintenance() {
        const rows = [];
        map.rides.forEach(ride => {
            rows.push(serializeRideMaintenance(ride));
        });
        rows.sort((a, b) => (b.downtime || 0) - (a.downtime || 0));
        return rows;
    }

    async function handleStartRideTest(params) {
        const { rideId } = params || {};
        if (typeof rideId !== "number") throw new Error("Missing or invalid parameter: rideId");
        try {
            await executeAction("ridesetstatus", { ride: rideId, status: 2 });
        } catch (e) {
            throw new Error(`Failed to start ride test: ${e.message}`);
        }
        return `Ride ${rideId} started in test mode.`;
    }

    async function handleCreateRide(params) {
        if (!params
            || typeof params.rideType !== "number"
            || typeof params.rideObject !== "number"
            || typeof params.entranceObject !== "number"
            || typeof params.colour1 !== "number"
            || typeof params.colour2 !== "number") {
            throw new Error("Missing or invalid parameters for createRide");
        }
        let result;
        try {
            result = await executeAction("ridecreate", {
                rideType: params.rideType,
                rideObject: params.rideObject,
                entranceObject: params.entranceObject,
                colour1: params.colour1,
                colour2: params.colour2,
                inspectionInterval: typeof params.inspectionInterval === "number" ? params.inspectionInterval : 2,
            });
        } catch (e) {
            throw new Error(`Failed to create ride: ${e.message}`);
        }
        if (typeof result.ride !== "number") throw new Error("Failed to create ride: no ride id returned");
        rideTrackStates.set(result.ride, { history: [], hasStationTrack: false });
        console.log(`Initialized fresh track state for ride ${result.ride}`);
        return { rideId: result.ride };
    }

    async function handleDeleteRide(params) {
        const { rideId } = params || {};
        if (typeof rideId !== "number") throw new Error("Missing or invalid parameter: rideId");
        const ride = map.getRide(rideId);
        if (!ride) throw new Error(`Ride ${rideId} not found`);
        await executeAction("ridedemolish", { ride: rideId, modifyType: 0 });
        rideTrackStates.delete(rideId);
        return { message: `Deleted ride ${rideId}` };
    }

    async function handleDeleteAllRides() {
        const rides = [];
        map.rides.forEach(r => rides.push(r));
        if (rides.length === 0) return "No rides to delete.";
        for (const ride of rides) {
            try {
                await executeAction("ridedemolish", { ride: ride.id, modifyType: 0 });
                rideTrackStates.delete(ride.id);
                console.log(`Cleared track state for deleted ride ${ride.id}`);
            } catch (e) {
                console.log(`Error demolishing ride ${ride.id}: ${e.message}`);
            }
        }
        return "Deleted all rides.";
    }

    function getRideTypeNumber(rideId) {
        const ride = map.getRide(rideId);
        if (!ride) throw new Error(`Ride ${rideId} not found`);
        return ride.type;
    }

    function getCandidateTrackTypes(rideId, lastPiece) {
        const all = context.getAllTrackSegments();
        if (!all || all.length === 0) return [0, 1, 2, 3];
        // Probe flat, station, quarter-turn, and common slope pieces — not every segment type.
        const unique = [...new Set(all.map(s => s.type))];
        const priority = [0, 1, 2, 3, 16, 17, 22, 23, 4, 5, 6, 7, 8, 9, 10];
        const ordered = [
            ...priority.filter(t => unique.includes(t)),
            ...unique.filter(t => !priority.includes(t)),
        ];
        return ordered.length > 0 ? ordered.slice(0, 24) : [0, 1, 2, 3];
    }

    function slimSegment(segment) {
        if (!segment) return null;
        return {
            type: segment.type,
            beginZ: segment.beginZ,
            endDirection: segment.endDirection,
            turnDirection: segment.turnDirection,
        };
    }

    function queryActionAsync(action, args) {
        return new Promise((resolve, reject) => {
            if (typeof context.queryAction !== "function") {
                reject(new Error("queryAction not available"));
                return;
            }
            context.queryAction(action, args, result => {
                if (!result || (result.error && result.error !== "")) {
                    reject(new Error((result && result.error) || "query failed"));
                } else {
                    resolve(result);
                }
            });
        });
    }

    function endpointPositionFromLastPiece(lastPiece) {
        return {
            x: lastPiece.nextX,
            y: lastPiece.nextY,
            z: lastPiece.nextZ,
            direction: lastPiece.nextDirection,
        };
    }

    function baseZForPlacement(trainEntryZ, segment) {
        return trainEntryZ - (segment.beginZ / 8);
    }

    async function probeTrackPlacement(rideId, lastPiece, trackType, rideTypeNum) {
        const segment = context.getTrackSegment(trackType);
        if (!segment) return null;

        const tileX = lastPiece.nextX;
        const tileY = lastPiece.nextY;
        const direction = lastPiece.nextDirection;
        const baseZ = baseZForPlacement(lastPiece.nextZ, segment);
        const placeArgs = {
            x: tileX * 32,
            y: tileY * 32,
            z: baseZ * 8,
            direction,
            ride: rideId,
            trackType,
            rideType: rideTypeNum,
            brakeSpeed: 0,
            colour: 0,
            seatRotation: 0,
            trackPlaceFlags: 0,
            isFromTrackDesign: true,
        };

        if (typeof context.queryAction === "function") {
            try {
                await queryActionAsync("trackplace", placeArgs);
                return serializeTrackSegment(segment);
            } catch (e) {
                return null;
            }
        }

        let result;
        try {
            result = await executeAction("trackplace", placeArgs);
        } catch (e) {
            return null;
        }

        const placed = findPlacedTrackElement(rideId, trackType, direction, result.position);
        if (!placed) {
            try {
                await executeAction("trackremove", {
                    x: tileX * 32,
                    y: tileY * 32,
                    z: baseZ * 8,
                    direction,
                    trackType,
                    sequence: 0,
                });
            } catch (e) { /* best effort */ }
            return null;
        }

        try {
            await executeAction("trackremove", {
                x: placed.tileX * 32,
                y: placed.tileY * 32,
                z: placed.element.baseZ,
                direction: placed.element.direction,
                trackType: placed.element.trackType,
                sequence: 0,
            });
        } catch (e) {
            return null;
        }

        return serializeTrackSegment(segment);
    }

    async function probeValidNextPieces(rideId, lastPiece) {
        const rideTypeNum = getRideTypeNumber(rideId);
        const candidates = getCandidateTrackTypes(rideId, lastPiece);
        const validPieces = [];
        const validSegments = [];
        for (const trackType of candidates) {
            const seg = await probeTrackPlacement(rideId, lastPiece, trackType, rideTypeNum);
            if (seg) {
                validPieces.push(trackType);
                validSegments.push(seg);
            }
        }
        return { validPieces, validSegments };
    }

    async function handleProbePlacePiece(params) {
        const { rideId, trackType } = params || {};
        if (typeof rideId !== "number" || typeof trackType !== "number") {
            throw new Error("Missing rideId or trackType");
        }
        const state = rideTrackStates.get(rideId);
        if (!state || !state.history || state.history.length === 0) {
            throw new Error("No track history to probe from");
        }
        const lastPiece = state.history[state.history.length - 1];
        const seg = await probeTrackPlacement(rideId, lastPiece, trackType, getRideTypeNumber(rideId));
        return { valid: seg !== null, segment: seg };
    }

    async function previewTrackPlacement(rideId, lastPiece, trackType, rideTypeNum) {
        const segment = context.getTrackSegment(trackType);
        if (!segment) return null;

        const tileX = lastPiece.nextX;
        const tileY = lastPiece.nextY;
        const direction = lastPiece.nextDirection;
        const baseZ = baseZForPlacement(lastPiece.nextZ, segment);
        const placeArgs = {
            x: tileX * 32,
            y: tileY * 32,
            z: baseZ * 8,
            direction,
            ride: rideId,
            trackType,
            rideType: rideTypeNum,
            brakeSpeed: 0,
            colour: 0,
            seatRotation: 0,
            trackPlaceFlags: 0,
            isFromTrackDesign: true,
        };

        let result;
        try {
            result = await executeAction("trackplace", placeArgs);
        } catch (e) {
            return null;
        }

        const placed = findPlacedTrackElement(rideId, trackType, direction, result.position);
        if (!placed) {
            try {
                await executeAction("trackremove", {
                    x: tileX * 32,
                    y: tileY * 32,
                    z: baseZ * 8,
                    direction,
                    trackType,
                    sequence: 0,
                });
            } catch (e) { /* best effort */ }
            return null;
        }

        const iteratorPos = { x: placed.tileX * 32, y: placed.tileY * 32 };
        const iterator = map.getTrackIterator(iteratorPos, placed.elementIndex);
        if (!iterator || !iterator.nextPosition) {
            try {
                await executeAction("trackremove", {
                    x: placed.tileX * 32,
                    y: placed.tileY * 32,
                    z: placed.element.baseZ,
                    direction: placed.element.direction,
                    trackType: placed.element.trackType,
                    sequence: 0,
                });
            } catch (e) { /* best effort */ }
            return null;
        }

        const nextTileX = Math.round(iterator.nextPosition.x / 32);
        const nextTileY = Math.round(iterator.nextPosition.y / 32);
        const nextTileZ = iterator.nextPosition.z / 8;
        const nextDirection = iterator.nextPosition.direction;
        const state = rideTrackStates.get(rideId);
        const firstPiece = state && state.firstPiece;
        const geometricallyClosed = Boolean(
            firstPiece
            && nextTileX === firstPiece.x
            && nextTileY === firstPiece.y
            && nextTileZ === firstPiece.z
            && nextDirection === firstPiece.direction,
        );
        const isCircuitComplete = geometricallyClosed && findStationPieces(rideId).length > 0;

        try {
            await executeAction("trackremove", {
                x: placed.tileX * 32,
                y: placed.tileY * 32,
                z: placed.element.baseZ,
                direction: placed.element.direction,
                trackType: placed.element.trackType,
                sequence: 0,
            });
        } catch (e) {
            return null;
        }

        return {
            valid: true,
            segment: serializeTrackSegment(segment),
            nextEndpoint: { x: nextTileX, y: nextTileY, z: nextTileZ, direction: nextDirection },
            isCircuitComplete,
        };
    }

    async function handlePreviewTrackPiece(params) {
        const { rideId, trackType } = params || {};
        if (typeof rideId !== "number" || typeof trackType !== "number") {
            throw new Error("Missing rideId or trackType");
        }
        const state = rideTrackStates.get(rideId);
        if (!state || !state.history || state.history.length === 0) {
            throw new Error("No track history to preview from");
        }
        const lastPiece = state.history[state.history.length - 1];
        const preview = await previewTrackPlacement(
            rideId,
            lastPiece,
            trackType,
            getRideTypeNumber(rideId),
        );
        if (!preview) {
            return { valid: false };
        }
        return preview;
    }

    async function handleGetValidNextPieces(params) {
        const { rideId } = params || {};
        if (typeof rideId !== "number") throw new Error("Missing or invalid parameter: rideId");
        if (!map.getRide(rideId)) throw new Error(`Ride ${rideId} not found`);

        const state = rideTrackStates.get(rideId);
        if (!state || !state.history || state.history.length === 0) {
            const initialTypes = [0, 1, 2, 3];
            const initialSegments = initialTypes
                .map(t => context.getTrackSegment(t))
                .filter(s => s)
                .map(serializeTrackSegment);
            return {
                validPieces: initialTypes,
                validSegments: initialSegments,
                lastTrackType: null,
                stateCategory: null,
                position: null,
                positionZIsTrainEntry: false,
                probeMethod: "initial",
            };
        }

        const lastPiece = state.history[state.history.length - 1];
        const segment = context.getTrackSegment(lastPiece.trackType);
        if (!segment) throw new Error(`Unknown track segment type: ${lastPiece.trackType}`);

        const position = endpointPositionFromLastPiece(lastPiece);

        if (typeof segment.getNextValidSegments === "function") {
            const follows = segment.getNextValidSegments(rideId);
            return {
                validPieces: follows.map(s => s.type),
                validSegments: follows.map(serializeTrackSegment),
                lastTrackType: lastPiece.trackType,
                stateCategory: null,
                position,
                positionZIsTrainEntry: true,
                probeMethod: "native",
            };
        }

        const probed = await probeValidNextPieces(rideId, lastPiece);
        return {
            validPieces: probed.validPieces,
            validSegments: probed.validSegments.map(slimSegment),
            lastTrackType: lastPiece.trackType,
            stateCategory: null,
            position,
            positionZIsTrainEntry: true,
            probeMethod: "probe",
        };
    }

    async function handleDeleteLastTrackPiece(params) {
        const { rideId } = params || {};
        if (typeof rideId !== "number") throw new Error("Missing or invalid parameter: rideId");
        const state = rideTrackStates.get(rideId);
        if (!state || !state.history || state.history.length === 0) {
            throw new Error(`No track pieces to delete for ride ${rideId}`);
        }
        const lastPiece = state.history[state.history.length - 1];
        console.log(
            `Attempting to remove track piece at tile: ${lastPiece.placedTileX} ${lastPiece.placedTileY} ` +
            `element index: ${lastPiece.elementIndex} trackType: ${lastPiece.trackType}`
        );
        try {
            await executeAction("trackremove", {
                x: lastPiece.placedTileX * 32,
                y: lastPiece.placedTileY * 32,
                z: lastPiece.z * 8,
                direction: lastPiece.direction,
                trackType: lastPiece.trackType,
                sequence: 0,
            });
        } catch (e) {
            console.log(`Failed to remove track piece: ${e.message}`);
            throw new Error(`Failed to remove track piece: ${e.message}`);
        }
        console.log("Successfully removed track piece");
        state.history.pop();
        if (state.history.length === 0) {
            state.firstPiece = null;
            state.isComplete = false;
        }
        const response = {
            message: `Track piece removed from ride ${rideId}`,
            piecesRemaining: state.history.length,
            nextEndpoint: null,
            lastTrackType: null,
        };
        if (state.history.length > 0) {
            const newLast = state.history[state.history.length - 1];
            response.nextEndpoint = {
                x: newLast.nextX,
                y: newLast.nextY,
                z: newLast.nextZ,
                direction: newLast.nextDirection,
            };
            response.lastTrackType = newLast.trackType;
        }
        return response;
    }

    // Search for the just-placed track element. First checks the central tile
    // with tolerance 8, then falls back to all 9 surrounding tiles (including
    // the center) with tolerance 16. Matches on ride id AND the freshly
    // requested trackType / direction / sequence===0 so we lock onto the
    // newly placed origin tile rather than a stale neighbour element on
    // dense or self-overlapping track. Returns { tile, element, elementIndex,
    // tileX, tileY } or null.
    // OpenRCT2 stores all station segment types (BeginStation=2,
    // MiddleStation=3, EndStation=1) as a single canonical station element
    // type (1). Other track types are stored unchanged. We canonicalize both
    // sides before comparing so a freshly placed BeginStation still matches.
    function canonicalTrackType(t) {
        return (t === 1 || t === 2 || t === 3) ? 1 : t;
    }

    function rotateOffset(x, y, direction) {
        switch (direction & 3) {
            case 1: return [y, -x];
            case 2: return [-x, -y];
            case 3: return [-y, x];
            default: return [x, y];
        }
    }

    function findPlacedTrackElement(rideId, trackType, direction, resultPosition) {
        const placedTileZ = resultPosition.z;
        const baseX = Math.floor(resultPosition.x / 32);
        const baseY = Math.floor(resultPosition.y / 32);
        const wantType = canonicalTrackType(trackType);

        const offsets = [
            [0, 0], [-1, 0], [1, 0], [0, -1], [0, 1],
            [-1, -1], [-1, 1], [1, -1], [1, 1],
        ];
        // Multi-tile pieces (corkscrews, loops, large turns) can put their
        // origin element outside the 3x3 ring and far from result z. Extend the
        // search with the segment's own element offsets and a z tolerance that
        // covers its height span.
        let zSpan = 16;
        const segment = context.getTrackSegment(trackType);
        if (segment) {
            zSpan = Math.max(16, Math.abs(segment.beginZ) + 8, Math.abs(segment.endZ) + 8);
            const seen = {};
            for (const o of offsets) seen[o[0] + "," + o[1]] = true;
            for (const el of (segment.elements || [])) {
                const r = rotateOffset(Math.round(el.x / 32), Math.round(el.y / 32), direction);
                for (const [ex, ey] of [r, [-r[0], -r[1]]]) {
                    const key = ex + "," + ey;
                    if (!seen[key]) {
                        seen[key] = true;
                        offsets.push([ex, ey]);
                    }
                }
            }
        }

        function matches(elem, tolerance) {
            if (elem.type !== "track" || elem.ride !== rideId) return false;
            if (Math.abs(elem.baseZ - placedTileZ) > tolerance) return false;
            if (canonicalTrackType(elem.trackType) !== wantType) return false;
            if (elem.direction !== direction) return false;
            // sequence may be undefined on older API versions; when present it
            // must be 0 to lock onto the origin tile of a multi-tile piece.
            if (typeof elem.sequence === "number" && elem.sequence !== 0) return false;
            return true;
        }

        function scan(tolerance, onlyCenter) {
            const list = onlyCenter ? [[0, 0]] : offsets;
            for (const [dx, dy] of list) {
                const tx = baseX + dx;
                const ty = baseY + dy;
                const tile = map.getTile(tx, ty);
                if (!tile) continue;
                for (let i = 0; i < tile.numElements; i++) {
                    if (matches(tile.elements[i], tolerance)) {
                        return { tile, element: tile.elements[i], elementIndex: i, tileX: tx, tileY: ty };
                    }
                }
            }
            return null;
        }

        return scan(8, true) || scan(16, false) || scan(zSpan, false);
    }

    async function handlePlaceTrackPiece(params) {
        const requiredParams = [
            "tileCoordinateX", "tileCoordinateY", "tileCoordinateZ", "direction", "ride",
            "trackType", "rideType", "brakeSpeed", "colour",
            "seatRotation", "trackPlaceFlags", "isFromTrackDesign",
        ];
        if (!params) throw new Error("Missing parameters for placeTrackPiece");
        for (const key of requiredParams) {
            if (typeof params[key] === "undefined") throw new Error(`Missing parameter: ${key}`);
        }

        // Continuity check: a placement must chain off the previous piece's
        // nextEndpoint. The trackplace action's z is the segment's base z,
        // but nextEndpoint.z is the train's entry z (high edge for descending
        // pieces). We translate via segment.beginZ (game units, 8 per tileZ)
        // so the comparison is apples-to-apples.
        const requestedSegment = context.getTrackSegment(params.trackType);
        if (!requestedSegment) throw new Error(`Unknown trackType: ${params.trackType}`);
        const requestedTrainEntryZ = params.tileCoordinateZ + (requestedSegment.beginZ / 8);
        const existingState = rideTrackStates.get(params.ride);
        if (existingState && existingState.history && existingState.history.length > 0) {
            const last = existingState.history[existingState.history.length - 1];
            if (params.tileCoordinateX !== last.nextX
                || params.tileCoordinateY !== last.nextY
                || requestedTrainEntryZ !== last.nextZ
                || params.direction !== last.nextDirection) {
                throw new Error(
                    `Placement does not continue from previous piece: `
                    + `train entry would be (${params.tileCoordinateX},${params.tileCoordinateY},${requestedTrainEntryZ}) dir=${params.direction}, `
                    + `previous piece ends at (${last.nextX},${last.nextY},${last.nextZ}) dir=${last.nextDirection}`,
                );
            }
        }

        const pixelCoordinateX = params.tileCoordinateX * 32;
        const pixelCoordinateY = params.tileCoordinateY * 32;
        const pixelCoordinateZ = params.tileCoordinateZ * 8;
        let flags = params.trackPlaceFlags;
        if (params.hasChainLift === true) flags = flags | 1;

        const isStationPiece = (params.trackType === 1 || params.trackType === 2 || params.trackType === 3);
        if (isStationPiece) {
            console.log(`Station piece placed - Type: ${params.trackType} for ride ${params.ride}`);
            console.log("Note: Use placeEntranceExit endpoint to add entrance/exit after station is complete");
        }

        let result;
        try {
            result = await executeAction("trackplace", {
                x: pixelCoordinateX,
                y: pixelCoordinateY,
                z: pixelCoordinateZ,
                direction: params.direction,
                ride: params.ride,
                trackType: params.trackType,
                rideType: params.rideType,
                brakeSpeed: params.brakeSpeed,
                colour: params.colour,
                seatRotation: params.seatRotation,
                trackPlaceFlags: flags,
                isFromTrackDesign: params.isFromTrackDesign,
            });
        } catch (e) {
            throw new Error(`Failed to place track piece: ${e.message}`);
        }

        console.log(`Track placed successfully at result position: ${JSON.stringify(result.position)}`);

        const placedTileX = Math.floor(result.position.x / 32);
        const placedTileY = Math.floor(result.position.y / 32);
        if (!map.getTile(placedTileX, placedTileY)) {
            throw new Error("Tile not found at placed position");
        }

        const placed = findPlacedTrackElement(params.ride, params.trackType, params.direction, result.position);
        if (!placed) throw new Error("Could not find track element on any nearby tile");
        console.log(`Found track element at index: ${placed.elementIndex} on tile: ${placed.tileX} ${placed.tileY}`);

        const iteratorPos = { x: placed.tileX * 32, y: placed.tileY * 32 };
        const iterator = map.getTrackIterator(iteratorPos, placed.elementIndex);
        if (!iterator) throw new Error("Track iterator not available");

        if (!iterator.nextPosition) {
            console.log("WARNING: Iterator exists but nextPosition is null. Track type:", placed.element.trackType);
            if (typeof iterator.next === "function") {
                iterator.next();
                if (!iterator.nextPosition) throw new Error("Track has no valid next position");
            } else {
                throw new Error("Track has no next position available");
            }
        }

        const nextTileX = Math.round(iterator.nextPosition.x / 32);
        const nextTileY = Math.round(iterator.nextPosition.y / 32);
        const nextTileZ = iterator.nextPosition.z / 8;
        const nextDirection = iterator.nextPosition.direction;

        // Initialize state if missing (e.g. ride created outside our flow).
        let state = rideTrackStates.get(params.ride);
        if (!state) {
            state = { history: [], hasStationTrack: false };
            rideTrackStates.set(params.ride, state);
        }
        if (isStationPiece || isStationTrackElement(placed.element)) {
            state.hasStationTrack = true;
            if (params.trackType === 2) {
                state.stationAnchor = {
                    x: placed.tileX,
                    y: placed.tileY,
                    z: placed.element.baseZ / 8,
                    direction: placed.element.direction,
                };
            }
        }
        // Record first piece's canonical input position, for circuit detection.
        // We use iterator.position (the placed segment's canonical input from
        // the engine) rather than the raw request params so direction masking
        // / coord normalization done by the engine can't cause subtle
        // mismatches against future nextEndpoint comparisons.
        if (!state.firstPiece) {
            state.firstPiece = {
                x: Math.round(iterator.position.x / 32),
                y: Math.round(iterator.position.y / 32),
                z: iterator.position.z / 8,
                direction: iterator.position.direction,
            };
        }

        const geometricallyClosed = Boolean(
            state.firstPiece
            && nextTileX === state.firstPiece.x
            && nextTileY === state.firstPiece.y
            && nextTileZ === state.firstPiece.z
            && nextDirection === state.firstPiece.direction
        );
        const isCircuitComplete = geometricallyClosed && Boolean(state.hasStationTrack);

        if (isCircuitComplete) {
            console.log("CIRCUIT COMPLETE! Track successfully connects back to station.");
        } else if (geometricallyClosed) {
            console.log("Geometric loop closed but no station track found — not marking circuit complete.");
        }

        state.history.push({
            x: params.tileCoordinateX,
            y: params.tileCoordinateY,
            z: params.tileCoordinateZ,
            direction: params.direction,
            trackType: params.trackType,
            hasChainLift: params.hasChainLift === true,
            nextX: nextTileX,
            nextY: nextTileY,
            nextZ: nextTileZ,
            nextDirection,
            elementIndex: placed.elementIndex,
            placedTileX: placed.tileX,
            placedTileY: placed.tileY,
        });
        state.isComplete = isCircuitComplete;

        const responsePayload = {
            message: `Track piece placed for ride ${params.ride}`,
            nextEndpoint: { x: nextTileX, y: nextTileY, z: nextTileZ, direction: nextDirection },
            isCircuitComplete,
            circuitMessage: isCircuitComplete
                ? "Circuit complete! Track connects back to station - ready for testing!"
                : "Continue building...",
            debug: {
                placedAt: { x: placed.tileX, y: placed.tileY, z: result.position.z },
                trackType: params.trackType,
                elemDirection: placed.element.direction,
            },
        };
        if (isStationPiece) responsePayload.stationDetected = true;
        return responsePayload;
    }

    function isStationTrackElement(elem) {
        if (elem.type !== "track") return false;
        // TrackElement.station is the authoritative station index (0, 1, …).
        if (elem.station !== null && elem.station !== undefined) return true;
        const t = canonicalTrackType(elem.trackType);
        return t === 1;
    }

    function stationTileHasRideTrack(rideId, x, y) {
        const tile = map.getTile(x, y);
        if (!tile) return false;
        for (let i = 0; i < tile.numElements; i++) {
            const elem = tile.elements[i];
            if (elem.type === "track" && elem.ride === rideId && isStationTrackElement(elem)) {
                return true;
            }
        }
        return false;
    }

    function stationPiecesFromRide(rideId) {
        const state = rideTrackStates.get(rideId);
        if (state?.stationAnchor && state.hasStationTrack) {
            return [{
                ...state.stationAnchor,
                trackType: 1,
                stationIndex: 0,
            }];
        }

        const ride = map.getRide(rideId);
        if (!ride || !ride.stations || ride.stations.length === 0) return [];
        const pieces = [];
        for (let si = 0; si < ride.stations.length; si++) {
            const st = ride.stations[si];
            if (!st || typeof st.length !== "number" || st.length <= 0) continue;
            let x, y, z, direction;
            if (st.start) {
                x = Math.round(st.start.x / 32);
                y = Math.round(st.start.y / 32);
                z = st.start.z / 8;
                direction = typeof st.start.direction === "number" ? st.start.direction : 0;
            } else if (state?.stationAnchor) {
                ({ x, y, z, direction } = state.stationAnchor);
            } else {
                continue;
            }
            pieces.push({
                x, y, z, direction,
                trackType: 1,
                stationIndex: si,
            });
            console.log(`Found station ${si} via ride.stations at ${x} ${y}`);
        }
        return pieces;
    }

    function findStationPieces(rideId) {
        const fromRide = stationPiecesFromRide(rideId);
        if (fromRide.length > 0) return fromRide;

        const stationPieces = [];
        for (let x = 0; x < map.size.x; x++) {
            for (let y = 0; y < map.size.y; y++) {
                const tile = map.getTile(x, y);
                if (!tile) continue;
                for (let i = 0; i < tile.numElements; i++) {
                    const elem = tile.elements[i];
                    if (elem.type === "track" && elem.ride === rideId && isStationTrackElement(elem)) {
                        stationPieces.push({
                            x, y,
                            z: elem.baseZ,
                            direction: elem.direction,
                            trackType: elem.trackType,
                            stationIndex: elem.station,
                        });
                        console.log(`Found station piece at ${x} ${y} direction: ${elem.direction} type: ${elem.trackType} station: ${elem.station}`);
                    }
                }
            }
        }
        return stationPieces;
    }

    // Entrance/exit direction points toward the station (0 = -x, 1 = +y, 2 = +x, 3 = -y);
    // facing away leaves the footpath unlinked and the queue without a banner.
    function entranceExitPositionsFor(stationTile) {
        const dir = stationTile.direction;
        if (dir === 0 || dir === 2) {
            // Track runs east-west, place perpendicular north-south
            return {
                entrance: { x: stationTile.x, y: stationTile.y - 1, direction: 1 },
                exit:     { x: stationTile.x, y: stationTile.y + 1, direction: 3 },
            };
        }
        // Track runs north-south, place perpendicular east-west
        return {
            entrance: { x: stationTile.x - 1, y: stationTile.y, direction: 2 },
            exit:     { x: stationTile.x + 1, y: stationTile.y, direction: 0 },
        };
    }

    async function tryPlaceEntranceOrExit(rideId, position, isExit, stationIndex = 0) {
        try {
            await executeAction("rideentranceexitplace", {
                x: position.x * 32,
                y: position.y * 32,
                direction: position.direction,
                ride: rideId,
                station: stationIndex,
                isExit,
            });
            console.log(`Successfully placed ${isExit ? "exit" : "entrance"} at ${position.x} ${position.y}`);
            return { x: position.x, y: position.y, direction: position.direction };
        } catch (e) {
            console.log(`Failed to place ${isExit ? "exit" : "entrance"} at ${position.x} ${position.y}: ${e.message}`);
            return null;
        }
    }

    function trackElementAtIterator(rideId, iterator) {
        const px = Math.round(iterator.position.x / 32);
        const py = Math.round(iterator.position.y / 32);
        const tile = map.getTile(px, py);
        if (!tile) return null;
        for (let i = 0; i < tile.numElements; i++) {
            const elem = tile.elements[i];
            if (elem.type === "track" && elem.ride === rideId) {
                return elem;
            }
        }
        return null;
    }

    function iteratorPositionKey(iterator) {
        const p = iterator.position;
        return `${Math.round(p.x / 32)},${Math.round(p.y / 32)},${Math.round(p.z / 8)},${p.direction}`;
    }

    function footprintFromCoords(origin, coords) {
        let minX = origin.x;
        let maxX = origin.x;
        let minY = origin.y;
        let maxY = origin.y;
        for (const c of coords) {
            if (typeof c.x === "number") {
                minX = Math.min(minX, c.x);
                maxX = Math.max(maxX, c.x);
            }
            if (typeof c.y === "number") {
                minY = Math.min(minY, c.y);
                maxY = Math.max(maxY, c.y);
            }
        }
        const width = Math.max(1, maxX - minX + 1);
        const height = Math.max(1, maxY - minY + 1);
        return {
            min_x: minX,
            min_y: minY,
            max_x: maxX,
            max_y: maxY,
            width,
            height,
            origin_offset: { x: origin.x - minX, y: origin.y - minY },
        };
    }

    function footprintFromHistory(state, origin) {
        const coords = [];
        for (const h of state.history) {
            coords.push(
                { x: h.x, y: h.y },
                { x: h.placedTileX, y: h.placedTileY },
                { x: h.nextX, y: h.nextY },
            );
        }
        return footprintFromCoords(origin, coords);
    }

    function exportDesignFromHistory(rideId, state, ride) {
        const first = state.firstPiece || {
            x: state.history[0].x,
            y: state.history[0].y,
            z: state.history[0].z,
            direction: state.history[0].direction,
        };
        const footprint = footprintFromHistory(state, first);
        return {
            version: 1,
            name: ride ? ride.name : `Ride ${rideId}`,
            ride_type: ride ? ride.type : getRideTypeNumber(rideId),
            ride_object: ride && typeof ride.object === "number" ? ride.object : 0,
            colour1: ride && typeof ride.colour1 === "number" ? ride.colour1 : 0,
            colour2: ride && typeof ride.colour2 === "number" ? ride.colour2 : 0,
            origin: {
                x: first.x,
                y: first.y,
                z: first.z,
                direction: first.direction,
            },
            pieces: state.history.map(h => ({
                track_type: h.trackType,
                has_chain_lift: h.hasChainLift === true,
            })),
            piece_count: state.history.length,
            circuit_complete: Boolean(state.isComplete),
            export_source: "history",
            footprint,
        };
    }

    function exportDesignFromIterator(rideId, ride) {
        const stations = findStationPieces(rideId);
        if (stations.length === 0) throw new Error(`No station pieces found for ride ${rideId}`);

        const start = stations[0];
        const tile = map.getTile(start.x, start.y);
        if (!tile) throw new Error("Station tile not found");
        let elementIndex = -1;
        for (let i = 0; i < tile.numElements; i++) {
            const elem = tile.elements[i];
            if (elem.type === "track" && elem.ride === rideId && isStationTrackElement(elem)) {
                elementIndex = i;
                break;
            }
        }
        if (elementIndex < 0) throw new Error("Station track element not found");

        const iterator = map.getTrackIterator({ x: start.x * 32, y: start.y * 32 }, elementIndex);
        if (!iterator) throw new Error("Track iterator unavailable for export");

        const originPos = iterator.position;
        const startKey = iteratorPositionKey(iterator);
        const pieces = [];
        const coords = [];
        let guard = 0;
        let stationPiecesSeen = 0;
        do {
            const pos = iterator.position;
            coords.push({
                x: Math.round(pos.x / 32),
                y: Math.round(pos.y / 32),
            });
            const elem = trackElementAtIterator(rideId, iterator);
            const seg = iterator.segment;
            let trackType = seg ? seg.type : (elem ? elem.trackType : 0);
            if (elem && isStationTrackElement(elem)) {
                stationPiecesSeen += 1;
                if (stationPiecesSeen === 1) trackType = 2;
                else if (stationPiecesSeen < (ride?.stations?.[0]?.length || 2)) trackType = 3;
                else trackType = 1;
            }
            pieces.push({
                track_type: trackType,
                has_chain_lift: Boolean(elem && elem.hasChainLift),
            });
            if (!iterator.next()) break;
            guard += 1;
        } while (guard < 512 && iteratorPositionKey(iterator) !== startKey);

        const origin = {
            x: Math.round(originPos.x / 32),
            y: Math.round(originPos.y / 32),
            z: Math.round(originPos.z / 8),
            direction: originPos.direction,
        };
        const footprint = footprintFromCoords(origin, coords);

        return {
            version: 1,
            name: ride ? ride.name : `Ride ${rideId}`,
            ride_type: ride ? ride.type : getRideTypeNumber(rideId),
            ride_object: ride && typeof ride.object === "number" ? ride.object : 0,
            colour1: ride && typeof ride.colour1 === "number" ? ride.colour1 : 0,
            colour2: ride && typeof ride.colour2 === "number" ? ride.colour2 : 0,
            origin,
            pieces,
            piece_count: pieces.length,
            circuit_complete: pieces.length > 3,
            export_source: "iterator",
            footprint,
        };
    }

    async function handleExportRideDesign(params) {
        const { rideId } = params || {};
        if (typeof rideId !== "number") throw new Error("Missing or invalid parameter: rideId");
        const ride = map.getRide(rideId);
        if (!ride) throw new Error(`Ride ${rideId} not found`);

        const state = rideTrackStates.get(rideId);
        if (state && state.history && state.history.length > 0) {
            return exportDesignFromHistory(rideId, state, ride);
        }
        return exportDesignFromIterator(rideId, ride);
    }

    async function placeDesignPiece(rideId, rideType, piece, position) {
        const segment = context.getTrackSegment(piece.track_type);
        if (!segment) throw new Error(`Unknown track_type ${piece.track_type}`);
        const baseZ = baseZForPlacement(position.z, segment);
        return handlePlaceTrackPiece({
            tileCoordinateX: position.x,
            tileCoordinateY: position.y,
            tileCoordinateZ: baseZ,
            direction: position.direction,
            ride: rideId,
            trackType: piece.track_type,
            rideType,
            brakeSpeed: 0,
            colour: 0,
            seatRotation: 0,
            trackPlaceFlags: 0,
            isFromTrackDesign: true,
            hasChainLift: piece.has_chain_lift === true,
        });
    }

    async function handlePlaceRideDesign(params) {
        const design = params && params.design;
        if (!design || !Array.isArray(design.pieces) || design.pieces.length === 0) {
            throw new Error("design.pieces must be a non-empty array");
        }

        const target = params.target || design.origin;
        if (!target
            || typeof target.x !== "number"
            || typeof target.y !== "number"
            || typeof target.z !== "number"
            || typeof target.direction !== "number") {
            throw new Error("target requires x, y, z, direction");
        }

        const rideType = typeof design.ride_type === "number" ? design.ride_type : 52;
        const created = await handleCreateRide({
            rideType,
            rideObject: typeof design.ride_object === "number" ? design.ride_object : 0,
            entranceObject: 0,
            colour1: typeof design.colour1 === "number" ? design.colour1 : 0,
            colour2: typeof design.colour2 === "number" ? design.colour2 : 0,
        });
        const rideId = created.rideId;

        const log = [];
        let result = await placeDesignPiece(rideId, rideType, design.pieces[0], target);
        log.push({ index: 0, track_type: design.pieces[0].track_type, ok: true });

        for (let i = 1; i < design.pieces.length; i++) {
            const piece = design.pieces[i];
            const state = rideTrackStates.get(rideId);
            if (!state || !state.history || state.history.length === 0) {
                throw new Error("Track state lost during design placement");
            }
            const last = state.history[state.history.length - 1];
            const pos = { x: last.nextX, y: last.nextY, z: last.nextZ, direction: last.nextDirection };
            try {
                result = await placeDesignPiece(rideId, rideType, piece, pos);
                log.push({ index: i, track_type: piece.track_type, ok: true });
            } catch (e) {
                try {
                    await handleDeleteRide({ rideId });
                } catch (delErr) { /* best effort */ }
                throw new Error(`Failed at piece ${i} (track_type ${piece.track_type}): ${e.message}`);
            }
        }

        let entranceExit = null;
        if (params.place_entrance_exit !== false) {
            entranceExit = await handlePlaceEntranceExit({ rideId });
        }

        let testResult = null;
        if (params.test_ride === true) {
            testResult = await handleStartRideTest({ rideId });
        }

        return {
            ride_id: rideId,
            piece_count: design.pieces.length,
            circuit_complete: Boolean(result && result.isCircuitComplete),
            placement_log: log,
            entrance_exit: entranceExit,
            test_result: testResult,
            target,
        };
    }

    async function handleProbeRideDesign(params) {
        const design = params && params.design;
        const target = params && params.target;
        if (!design || !target) throw new Error("probeRideDesign requires design and target");
        try {
            const placed = await handlePlaceRideDesign({
                design,
                target,
                place_entrance_exit: false,
                test_ride: false,
            });
            const state = rideTrackStates.get(placed.ride_id);
            const footprint = state && state.history && state.history.length > 0
                ? footprintFromHistory(state, target)
                : (design.footprint || null);
            await handleDeleteRide({ rideId: placed.ride_id });
            return {
                ok: true,
                target,
                footprint,
                circuit_complete: placed.circuit_complete,
                piece_count: placed.piece_count,
            };
        } catch (e) {
            return { ok: false, target, error: e.message };
        }
    }

    async function handlePlaceEntranceExit(params) {
        const { rideId } = params || {};
        if (typeof rideId !== "number") throw new Error("Missing or invalid parameter: rideId");

        if (!map.getRide(rideId)) throw new Error(`Ride ${rideId} not found`);

        const stationPieces = findStationPieces(rideId);
        if (stationPieces.length === 0) throw new Error(`No station pieces found for ride ${rideId}`);
        console.log(`Found ${stationPieces.length} station pieces total`);

        const ride = map.getRide(rideId);
        let entrance = null, exit = null;
        for (let i = 0; i < stationPieces.length; i++) {
            const piece = stationPieces[i];
            const stationIdx = typeof piece.stationIndex === "number" ? piece.stationIndex : 0;
            const positions = entranceExitPositionsFor(piece);
            if (!entrance) {
                entrance = await tryPlaceEntranceOrExit(rideId, positions.entrance, false, stationIdx);
            }
            if (!exit) {
                exit = await tryPlaceEntranceOrExit(rideId, positions.exit, true, stationIdx);
            }
            if (entrance && exit) break;
        }
        // Prefer engine station anchors when entrance/exit offsets fail.
        if ((!entrance || !exit) && ride && ride.stations && ride.stations.length > 0) {
            for (let si = 0; si < ride.stations.length; si++) {
                const st = ride.stations[si];
                if (!st || !st.start) continue;
                const sx = Math.round(st.start.x / 32);
                const sy = Math.round(st.start.y / 32);
                const dir = typeof st.start.direction === "number" ? st.start.direction : 0;
                const anchor = { x: sx, y: sy, direction: dir };
                const positions = entranceExitPositionsFor(anchor);
                if (!entrance) entrance = await tryPlaceEntranceOrExit(rideId, positions.entrance, false, si);
                if (!exit) exit = await tryPlaceEntranceOrExit(rideId, positions.exit, true, si);
                if (entrance && exit) break;
            }
        }

        if (entrance && exit) {
            return { entrance, exit, message: "Successfully placed entrance and exit" };
        }
        if (entrance || exit) {
            return {
                entrance, exit,
                warning: "Only partially successful - "
                    + (!entrance ? "Could not place entrance. " : "")
                    + (!exit ? "Could not place exit." : ""),
            };
        }
        throw new Error("Failed to place entrance and exit. No valid positions found near any station piece.");
    }
}

// Register the plugin
registerPlugin({
 name: "Ride Builder",
 version: "1.0.0",
 authors: ["openrct2-mods"],
 type: "intransient",
 licence: "MIT",
 // Develop is currently API 116 (#26560). #26675 added ride/context helpers without a bump.
 minApiVersion: 114,
 targetApiVersion: 116,
 main: main
});
