import React from 'react';
import { createRoot } from 'react-dom/client';
import App from './app/App';
import 'ol/ol.css';
import './app/styles.css';
import { applyTheme, readTheme } from './app/themeMode';

// Before the first render, so a dark page never flashes light.
applyTheme(readTheme());

createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>);

