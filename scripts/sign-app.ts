import path from 'node:path';
import { root } from '../src/config.ts';
import { signApp } from './signing.ts';

await signApp(path.join(root, 'PersonalAssistant.app'));
