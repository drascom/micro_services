import { createHash } from "node:crypto";
import { spawn } from "node:child_process";
import {
  access,
  cp,
  mkdir,
  readdir,
  readFile,
  rename,
  rm,
  writeFile
} from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { create as createTar } from "tar";
import { assertPanelGraph, panelDigest } from "./panel-graph.mjs";
import { assertRuntimeFileList, assertRuntimeModuleGraph } from "./runtime-module-graph.mjs";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const runtimeSource = path.join(projectRoot, "apps", "runtime");
const generatedRoot = path.join(projectRoot, "apps", "android", "app", "build", "generated", "node-assets");
const stagingRoot = `${generatedRoot}.staging-${process.pid}`;
const stagedProject = path.join(stagingRoot, "nodejs-project");
const assetArchive = path.join(stagingRoot, "nodejs-project.tgz");
const bundledCore = path.join(stagedProject, "villa-bridge");
const lazySerialPortPatch = path.join(
  projectRoot,
  "apps",
  "android",
  "patches",
  "zigbee-herdsman-serialPort.js"
);

// Ilk acilis sablonlari da pakete girer: tablette kabuk yok, `install.sh` yok.
// Bunlar eksik olursa ilk acilis tohumlamasi (first-run.cjs) cihazda coker.
const requiredFiles = [
  "dashboard.html",
  "first-run.cjs",
  "intl-fallback.cjs",
  "lan-discovery.cjs",
  "main.cjs",
  "matter-bigint-guard.cjs",
  "orchestration.cjs",
  "failover-net.cjs",
  "failover-policy.cjs",
  "failover-runtime.cjs",
  "failover-sync.cjs",
  "failover.cjs",
  "peer-watch.cjs",
  "tz-rules.cjs",
  "package-lock.json",
  "package.json",
  "templates/configuration.example.yaml",
  "templates/provisioning.example.json",
  "templates/villa-bridge.example.yaml"
];

async function run(command, arguments_, options = {}) {
  await new Promise((resolve, reject) => {
    const child = spawn(command, arguments_, {
      stdio: "inherit",
      ...options
    });
    child.once("error", reject);
    child.once("exit", (code, signal) => {
      if (code === 0) {
        resolve();
        return;
      }
      reject(new Error(
        signal
          ? `${command} was terminated by ${signal}`
          : `${command} exited with code ${code}`
      ));
    });
  });
}

async function sha256(file) {
  const content = await readFile(file);
  return createHash("sha256").update(content).digest("hex");
}

async function replaceExactly(file, expected, replacement) {
  const source = await readFile(file, "utf8");
  const occurrences = source.split(expected).length - 1;
  if (occurrences !== 1) {
    throw new Error(`Expected one Android compatibility match in ${file}, found ${occurrences}`);
  }
  await writeFile(file, source.replace(expected, replacement), "utf8");
}

async function replaceAllExactly(file, expected, replacement, expectedOccurrences) {
  const source = await readFile(file, "utf8");
  const occurrences = source.split(expected).length - 1;
  if (occurrences !== expectedOccurrences) {
    throw new Error(
      `Expected ${expectedOccurrences} Android compatibility matches in ${file}, found ${occurrences}`
    );
  }
  await writeFile(file, source.split(expected).join(replacement), "utf8");
}

async function pruneRuntimeMetadata(directory) {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const target = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      await pruneRuntimeMetadata(target);
      continue;
    }
    if (
      entry.name.endsWith(".map") ||
      entry.name.endsWith(".d.ts") ||
      entry.name.endsWith(".d.mts") ||
      entry.name.endsWith(".d.cts")
    ) {
      await rm(target, { force: true });
    }
  }
}

async function prepare() {
  for (const file of requiredFiles) {
    await access(path.join(runtimeSource, file));
  }
  await access(path.join(projectRoot, "public", "index.html"));
  await access(lazySerialPortPatch);
  // Panel parcalari eksik/bozuksa APK'ya yarim panel girmesin.
  await assertPanelGraph(projectRoot);
  // Yeni bir runtime modulu eklenip listeye yazilmadiysa paketleme burada durur.
  const sourceGraph = await assertRuntimeFileList(runtimeSource, requiredFiles);
  if (sourceGraph.dynamic.length > 0) {
    console.warn(
      `Uyari: dinamik require iceren runtime modulleri statik olarak dogrulanamadi: ${sourceGraph.dynamic.join(", ")}`
    );
  }

  await rm(stagingRoot, { recursive: true, force: true });
  await mkdir(stagedProject, { recursive: true });

  try {
    const npm = process.platform === "win32" ? "npm.cmd" : "npm";
    await run(npm, ["run", "build"], { cwd: projectRoot });
    await access(path.join(projectRoot, "dist", "index.js"));

    for (const file of requiredFiles) {
      const target = path.join(stagedProject, file);
      // `templates/` gibi alt dizinler icin hedef dizin once acilmali.
      await mkdir(path.dirname(target), { recursive: true });
      await cp(path.join(runtimeSource, file), target);
    }
    await mkdir(bundledCore, { recursive: true });
    await cp(path.join(projectRoot, "dist"), path.join(bundledCore, "dist"), {
      recursive: true
    });
    await cp(path.join(projectRoot, "public"), path.join(bundledCore, "public"), {
      recursive: true
    });
    await writeFile(
      path.join(bundledCore, "package.json"),
      `${JSON.stringify({
        name: "villa-bridge-embedded-core",
        private: true,
        type: "module"
      }, null, 2)}\n`,
      "utf8"
    );

    await run(
      npm,
      ["ci", "--omit=dev", "--ignore-scripts", "--no-audit", "--no-fund"],
      { cwd: stagedProject }
    );
    await cp(
      lazySerialPortPatch,
      path.join(
        stagedProject,
        "node_modules",
        "zigbee-herdsman",
        "dist",
        "adapter",
        "serialPort.js"
      )
    );
    for (const moduleKind of ["cjs", "esm"]) {
      await replaceExactly(
        path.join(
          stagedProject,
          "node_modules",
          "matterbridge",
          "node_modules",
          "@matter",
          "general",
          "dist",
          moduleKind,
          "util",
          "GeneratedClass.js"
        ),
        "/^[\\p{L}0-9$_]+$/u",
        "/^[A-Za-z0-9$_]+$/"
      );
    }
    const zigbeeMatterPlatform = path.join(
      stagedProject,
      "node_modules",
      "matterbridge-zigbee2mqtt",
      "dist",
      "platform.js"
    );
    await replaceExactly(
      zigbeeMatterPlatform,
      "ID: ${device.ieee_address}: ${error}`);",
      "ID: ${device.ieee_address}: ${error instanceof Error ? error.stack : error}`);"
    );
    await replaceExactly(
      zigbeeMatterPlatform,
      "ID: ${group.id}: ${error}`);",
      "ID: ${group.id}: ${error instanceof Error ? error.stack : error}`);"
    );
    const zigbeeMatterEntity = path.join(
      stagedProject,
      "node_modules",
      "matterbridge-zigbee2mqtt",
      "dist",
      "entity.js"
    );
    await replaceExactly(
      zigbeeMatterEntity,
      "const tagList = [];\n                    if (endpoint === 'l1')",
      "const tagList = [];\n                    const endpointLabel = device.endpoint_names?.[endpoint] ?? 'endpoint ' + endpoint;\n                    if (endpoint === 'l1')"
    );
    await replaceAllExactly(
      zigbeeMatterEntity,
      "label: 'endpoint ' + endpoint",
      "label: endpointLabel",
      7
    );
    await pruneRuntimeMetadata(stagedProject);
    await run(
      process.execPath,
      [
        "-e",
        [
          "const Module = require('node:module');",
          "const original = Module._load;",
          "Module._load = function(request, parent, isMain) {",
          "  if (request === '@serialport/bindings-cpp') throw new Error('Serial binding loaded eagerly');",
          "  return original.call(this, request, parent, isMain);",
          "};",
          "require('zigbee-herdsman/dist/adapter/adapterDiscovery.js');"
        ].join("\n")
      ],
      { cwd: stagedProject }
    );
    await run(process.execPath, ["--check", path.join(stagedProject, "main.cjs")]);
    // `--check` yalnizca sozdizimi bakar; paketin modul grafigi de cozulebilmeli.
    await assertRuntimeModuleGraph(stagedProject);

    const packageJson = JSON.parse(await readFile(path.join(stagedProject, "package.json"), "utf8"));
    // Panel tek dosya degil: index.html + public/css + public/js. Ozet hepsini kapsar,
    // dosya sirasi alfabetik goreli yol oldugu icin sonuc platformdan bagimsiz deterministiktir.
    const panel = await panelDigest(bundledCore);
    const manifest = {
      format: 1,
      runtime: packageJson.name,
      version: packageJson.version,
      node: packageJson.engines?.node || null,
      dependencies: packageJson.dependencies || {},
      entrypoint: "main.cjs",
      entrypointSha256: await sha256(path.join(stagedProject, "main.cjs")),
      coreEntrypoint: "villa-bridge/dist/index.js",
      coreEntrypointSha256: await sha256(
        path.join(bundledCore, "dist", "index.js")
      ),
      dashboard: "villa-bridge/public/index.html",
      dashboardFiles: panel.files.map((file) => `villa-bridge/public/${file}`),
      dashboardSha256: panel.sha256
    };
    await writeFile(
      path.join(stagedProject, "asset-manifest.json"),
      `${JSON.stringify(manifest, null, 2)}\n`,
      "utf8"
    );
    await createTar(
      {
        cwd: stagingRoot,
        file: assetArchive,
        follow: true,
        gzip: { level: 6 },
        noMtime: true,
        noPax: true,
        portable: true
      },
      ["nodejs-project"]
    );
    await rm(stagedProject, { recursive: true, force: true });

    await mkdir(path.dirname(generatedRoot), { recursive: true });
    await rm(generatedRoot, { recursive: true, force: true });
    await rename(stagingRoot, generatedRoot);
    console.log(`Prepared Android Node.js assets in ${generatedRoot}`);
  } catch (error) {
    await rm(stagingRoot, { recursive: true, force: true });
    throw error;
  }
}

prepare().catch((error) => {
  console.error("Unable to prepare Android Node.js assets:", error);
  process.exitCode = 1;
});
