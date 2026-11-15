"""
Dynamic imports: ``import('./views/Home.vue')`` names a module as surely as a static import.
Vue Router and React lazy routes are written this way, so before 0.18.1 the router's
dependency on every page it loads was missing, and so were packages a library loads only
when they are used (NestJS's Kafka, RabbitMQ and Redis transports).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from archilens.analyzers.external import match_system
from archilens.analyzers.treesitter import DYNAMIC_IMPORT, extract_from_file
from archilens.config import ArchiLensConfig
from archilens.engine import analyze_repository
from archilens.models import ArchSnapshot

SAMPLE = """import Base from './base.vue'
const Home = () => import('./views/Home.vue');
const routes = [{ path: '/a', component: () => import(/* webpackChunkName: "a" */ "@/views/A.vue") }];
const B = () => import(
  /* webpackChunkName: "b" */
  /* webpackPrefetch: true */
  './views/B.vue'
);
await import(`./locale/${lang}.js`);
const x = obj.import('not-an-import');
const y = $import('not-an-import-either');
loadPackage('kafkajs', 'ClientKafka', () => import('kafkajs'));
const json = await import('./data.json', { with: { type: 'json' } });
// import('./commented-out.js')
const s = "import('./in-a-string.js')";
"""

FOUND = {"./base.vue", "./views/Home.vue", "@/views/A.vue", "./views/B.vue", "kafkajs", "./data.json"}


def _repo(root: Path, files: dict[str, str]) -> Path:
    for rel, body in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(body, encoding="utf-8")
    return root


def _analyze(repo: Path) -> ArchSnapshot:
    return analyze_repository(repo, ArchiLensConfig(), use_ai=False)


def _module_edges(snap: ArchSnapshot) -> dict[tuple[str, str], int]:
    return {
        (e.source.removeprefix("module:"), e.target.removeprefix("module:")): e.weight
        for e in snap.edges
        if e.source.startswith("module:") and e.target.startswith("module:")
    }


@pytest.mark.parametrize("language", ["javascript", "typescript"])
def test_literal_paths_are_read_and_nothing_else(language):
    result = extract_from_file(Path("x"), language, SAMPLE)
    assert result.used_treesitter
    # Not read: a template literal (known only at run time), a method or function that is
    # merely named import, and anything in a comment or a string.
    assert {i.path for i in result.imports} == FOUND


def test_the_pattern_alone_without_a_syntax_tree():
    # The regex fallback sees comments and strings too, so only check what the pattern decides.
    found = {m.group(1) for m in DYNAMIC_IMPORT.finditer(SAMPLE)}
    assert found - {"./commented-out.js", "./in-a-string.js"} == FOUND - {"./base.vue"}
    assert "not-an-import" not in found and "not-an-import-either" not in found


def test_vue_router_lazy_routes_are_dependencies(tmp_path):
    snap = _analyze(_repo(tmp_path, {
        "src/router/index.js": (
            "import Vue from 'vue'\n"
            "import Layout from '@/layout/index.vue'\n"
            "export const routes = [\n"
            "  { path: '/', component: Layout, children: [\n"
            "    { path: 'home', component: () => import('@/views/Home.vue') },\n"
            "    { path: 'about', component: () => import(/* webpackChunkName: \"about\" */ '../views/about.vue') },\n"
            "  ] },\n"
            "]\n"
        ),
        "src/layout/index.vue": "<template><div/></template>\n",
        "src/views/Home.vue": "<template><div/></template>\n",
        "src/views/about.vue": "<template><div/></template>\n",
    }))
    edges = _module_edges(snap)
    assert edges[("src/router", "src/views")] == 2  # both lazy routes; before, none
    assert edges[("src/router", "src/layout")] == 1


def test_react_lazy_and_a_vue_component_script(tmp_path):
    snap = _analyze(_repo(tmp_path, {
        "src/app/router.ts": "import { lazy } from 'react'\nexport const Page = lazy(() => import('../pages/page'))\n",
        "src/pages/page.ts": "export default function Page() { return null }\n",
        "src/components/Chart.vue": "<script setup>\nconst load = () => import('../charts/line.js')\n</script>\n",
        "src/charts/line.js": "export function line() {}\n",
    }))
    edges = _module_edges(snap)
    assert ("src/app", "src/pages") in edges
    assert ("src/components", "src/charts") in edges


def test_a_package_loaded_on_demand_is_an_external_system(tmp_path):
    snap = _analyze(_repo(tmp_path, {
        "packages/microservices/client-kafka.ts": (
            "export async function connect() {\n"
            "  const { Kafka } = await loadPackage('kafkajs', 'ClientKafka', () => import('kafkajs'));\n"
            "}\n"
        ),
        "packages/microservices/client-nats.ts": "export const load = () => import('@nats-io/transport-node');\n",
        "packages/microservices/client-mqtt.ts": "export const load = () => import('mqtt');\n",
    }))
    assert {s.name: s.packages for s in snap.detected_systems} == {
        "Kafka": ["kafkajs"],
        "MQTT broker": ["mqtt"],
        "NATS": ["@nats-io"],
    }


@pytest.mark.parametrize(
    ("imported", "language", "system"),
    [
        ("@nats-io/transport-node", "typescript", "NATS"),  # NATS.js 3 packages
        ("@nats-io/jetstream", "javascript", "NATS"),
        ("nats", "javascript", "NATS"),
        ("mqtt", "javascript", "MQTT broker"),
        ("paho.mqtt.client", "python", "MQTT broker"),
        ("aiomqtt", "python", "MQTT broker"),
        ("org.eclipse.paho.client.mqttv3.MqttClient", "java", "MQTT broker"),
        ("github.com/eclipse/paho.mqtt.golang", "go", "MQTT broker"),
        ("mqtt-packet", "javascript", None),  # a parser, not a client; and not the "mqtt" root
        ("@nats-iox/client", "javascript", None),
    ],
)
def test_catalog_nats_and_mqtt(imported, language, system):
    match = match_system(imported, language)
    assert (match[0].name if match else None) == system
