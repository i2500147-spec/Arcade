const tg = window.Telegram.WebApp;
tg.expand();
tg.ready();

let userId = 0;
let userBalance = 0;
let refCode = '';
let refEarned = 0;
let crashActive = false;
let crashMultiplier = 1;
let crashBet = 0;
let crashInterval = null;
let slotMult = 1;
let currentSlot = 'novice';
let upgradeFrom = null;
let upgradeTo = null;
let openingCase = false;
let currentCase = '';

const API = 'https://arcade-8ru7.onrender.com/api';

const initData = tg.initDataUnsafe;
if (initData.user) {
    userId = initData.user.id;
    document.getElementById('avatar').src = initData.user.photo_url || '';
    loadUserData();
}

async function loadUserData() {
    try {
        const res = await fetch(`${API}/user/${userId}`);
        const data = await res.json();
        userBalance = data.balance || 0;
        refCode = data.ref_code || '';
        refEarned = data.ref_earned || 0;
        updateUI();
    } catch(e) {}
}

function updateUI() {
    document.getElementById('balance').textContent = userBalance;
    document.getElementById('ref-earned').textContent = refEarned;
}

function goPage(page) {
    if (crashActive || openingCase) return;
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    const el = document.getElementById('page-' + page);
    if (el) el.classList.add('active');
    document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
    const navMap = { ref: 0, home: 1, leaderboard: 2, inventory: 3, shop: 4 };
    if (navMap[page] !== undefined) document.querySelectorAll('.nav-btn')[navMap[page]].classList.add('active');
    if (page === 'leaderboard') loadLeaderboard();
    if (page === 'inventory') loadInventory();
    if (page === 'shop') loadShop();
    if (page === 'ref') loadRefCode();
}

async function loadRefCode() { if (!refCode) tg.sendData(JSON.stringify({ action: 'get_ref' })); }
function copyRef() {
    navigator.clipboard.writeText(`https://t.me/arcadecasinobot?start=ref_${refCode}`).then(() => tg.showAlert('Скопировано!'));
}

function openDeposit() { document.getElementById('page-deposit').classList.add('active'); }
function closeDeposit() { document.getElementById('page-deposit').classList.remove('active'); }

function deposit() {
    const amount = parseInt(document.getElementById('dep-amount').value);
    if (!amount || amount < 1) { tg.showAlert('Введите сумму!'); return; }
    tg.sendData(JSON.stringify({ action: 'pay', amount: amount }));
    tg.showAlert('Счёт открыт!');
    closeDeposit();
}

function usePromo() {
    const code = document.getElementById('promo-code').value.trim().toUpperCase();
    if (!code) { tg.showAlert('Введите промокод!'); return; }
    tg.sendData(JSON.stringify({ action: 'promo', code: code }));
    document.getElementById('promo-code').value = '';
}

function openCrash() { document.getElementById('page-crash').classList.add('active'); }
function openSlots() {
    document.getElementById('page-slots').classList.add('active');
    buildSlotReels();
}
function openUpgrade() { document.getElementById('page-upgrade').classList.add('active'); }
function closeUpgrade() { document.getElementById('page-upgrade').classList.remove('active'); }
function openDuel() { tg.showAlert('Дуэли скоро!'); }

// ==================== КРАШ ====================
function startCrash() {
    if (crashActive) return;
    const bet = parseInt(document.getElementById('crash-bet').value);
    if (!bet || bet < 1) { tg.showAlert('Введите ставку!'); return; }
    if (bet > userBalance) { tg.showAlert('Недостаточно звёзд!'); return; }
    crashBet = bet; crashMultiplier = 1; crashActive = true;
    userBalance -= bet; updateUI();
    document.getElementById('crash-cashout').disabled = false;
    document.getElementById('crash-mult').style.color = '#27ae60';
    crashInterval = setInterval(() => {
        crashMultiplier += 0.05;
        document.getElementById('crash-mult').textContent = crashMultiplier.toFixed(2) + 'x';
        drawCrashGraph();
        if (Math.random() < 0.02 * crashMultiplier) endCrash(false);
    }, 200);
}

function drawCrashGraph() {
    const graph = document.getElementById('crash-graph');
    const bar = document.createElement('div');
    bar.style.cssText = `width:4px;height:${Math.min(crashMultiplier*15,100)}px;background:#27ae60;display:inline-block;margin:1px;vertical-align:bottom`;
    graph.appendChild(bar);
    if (graph.children.length > 80) graph.removeChild(graph.firstChild);
}

function cashout() { if (crashActive) endCrash(true); }

function endCrash(won) {
    clearInterval(crashInterval); crashActive = false;
    document.getElementById('crash-cashout').disabled = true;
    document.getElementById('crash-graph').innerHTML = '';
    if (won) {
        const win = Math.floor(crashBet * crashMultiplier);
        userBalance += win;
        document.getElementById('crash-mult').textContent = 'Выигрыш: ' + win + ' ⭐';
        document.getElementById('crash-mult').style.color = '#27ae60';
    } else {
        document.getElementById('crash-mult').textContent = 'Краш!';
        document.getElementById('crash-mult').style.color = '#c0392b';
    }
    updateUI();
}

function closeGame() {
    if (crashActive) { tg.showAlert('Дождитесь конца!'); return; }
    document.getElementById('page-crash').classList.remove('active');
    document.getElementById('page-slots').classList.remove('active');
    goPage('home');
}

// ==================== СЛОТЫ ====================
function buildSlotReels() {
    const count = slotMult * 3;
    let html = '';
    for (let i = 0; i < count; i++) html += '<div class="slot-reel">⭐</div>';
    document.getElementById('slot-reels').innerHTML = html;
}

function switchSlot(type) {
    currentSlot = type;
    document.querySelectorAll('.slot-tab').forEach(t => t.classList.remove('active'));
    event.target.classList.add('active');
    buildSlotReels();
    const colors = { novice: '#2d7dd2', start: '#c0392b', major: '#d4a843' };
    document.getElementById('slot-reels').style.borderColor = colors[type];
}

function setSlotMult(m) {
    slotMult = m;
    document.querySelectorAll('.mult-btn').forEach(b => b.classList.remove('active'));
    event.target.classList.add('active');
    buildSlotReels();
}

function spinSlots() {
    const prices = { novice: 3, start: 10, major: 305 };
    const price = prices[currentSlot] * slotMult;
    if (price > userBalance) { tg.showAlert('Недостаточно звёзд!'); return; }
    userBalance -= price; updateUI();
    
    const items = { novice: ['⭐','💫','💎','🎁','💰'], start: ['⭐','💫','💎','🔥','💰','👑','💍','🎉'], major: ['🔥','💎'] };
    const reels = document.querySelectorAll('.slot-reel');
    let count = 0;
    const spin = setInterval(() => {
        reels.forEach(r => r.textContent = items[currentSlot][Math.floor(Math.random() * items[currentSlot].length)]);
        count++;
        if (count > 30) {
            clearInterval(spin);
            const vals = { novice: [1,3,5,10,305], start: [1,2,3,5,10,25,305,500], major: [305,800] };
            let totalWin = 0;
            reels.forEach(() => totalWin += vals[currentSlot][Math.floor(Math.random() * vals[currentSlot].length)]);
            userBalance += totalWin;
            updateUI();
            tg.showAlert(`Выигрыш: ${totalWin}⭐!`);
        }
    }, 80);
}

// ==================== КЕЙСЫ ====================
function showCaseDetail(name) {
    currentCase = name;
    const cases = {
        valera: {icon:'👤',name:'Валера',price:3,items:['⭐ 3','⭐ 5','⭐ 10']},
        bumzhikha: {icon:'👩',name:'Бомжиха',price:5,items:['⭐ 5','⭐ 15','⭐ 30','⭐ 50']},
        svidanie: {icon:'💝',name:'Свидание',price:50,items:['⭐ 5','⭐ 4','⭐ 3','⭐ 7','⭐ 50','⭐ 75','⭐ 100','⭐ 200']},
        otel: {icon:'🏨',name:'Отель',price:75,items:['⭐ 5','⭐ 4','⭐ 3','⭐ 1','⭐ 80','⭐ 150','⭐ 200']},
        forever: {icon:'💎',name:'Forever',price:300,items:['⭐ 1','⭐ 10','⭐ 4','⭐ 350','⭐ 400','⭐ 500','⭐ 1000']},
        allornothing: {icon:'🎰',name:'Всё или ничего',price:2000,items:['⭐ 1000','⭐ 5000']}
    };
    const c = cases[name];
    document.getElementById('case-detail-content').innerHTML = `
        <div style="text-align:center;font-size:60px;margin-bottom:12px;">${c.icon}</div>
        <div style="text-align:center;font-size:22px;font-weight:bold;color:var(--gold);">${c.name}</div>
        <div style="text-align:center;color:var(--gray);margin-bottom:16px;">${c.price}⭐</div>
        <div style="display:flex;flex-wrap:wrap;gap:8px;justify-content:center;margin-bottom:20px;">
            ${c.items.map(i => `<div style="background:var(--border);padding:10px 14px;border-radius:10px;font-size:14px;">${i}</div>`).join('')}
        </div>
        <button class="btn-gold big" onclick="openCase('${name}')">Открыть</button>
    `;
    goPage('case-detail');
}

function openFreeCase() { if (!openingCase) { openingCase = true; startCaseAnim(); tg.sendData(JSON.stringify({ action: 'open_case', case: 'daily' })); } }
function openCase(name) { if (!openingCase) { openingCase = true; startCaseAnim(); tg.sendData(JSON.stringify({ action: 'open_case', case: name })); } }

function startCaseAnim() {
    document.getElementById('page-case-open').classList.add('active');
    document.getElementById('case-result-block').style.display = 'none';
    document.getElementById('case-reels').innerHTML = '';
    for (let i = 0; i < 5; i++) {
        const reel = document.createElement('div');
        reel.className = 'case-reel-item';
        reel.textContent = ['📦','💎','⭐','💫','🔥','💰','👑'][Math.floor(Math.random()*7)];
        document.getElementById('case-reels').appendChild(reel);
    }
}

function closeCaseOpen() {
    if (openingCase) return;
    document.getElementById('page-case-open').classList.remove('active');
    goPage('home');
}

// ==================== ИНВЕНТАРЬ ====================
async function loadInventory() {
    const res = await fetch(`${API}/inventory/${userId}`);
    const items = await res.json();
    const list = document.getElementById('inventory-list');
    if (!items.length) { list.innerHTML = '<p class="empty">Здесь пусто</p>'; return; }
    list.innerHTML = items.map(i => `
        <div class="inv-item">
            <div class="nft-icon nft-${i.icon||'default'}"></div>
            <span class="inv-name">${i.name}</span>
            <span class="inv-value">${i.value}⭐</span>
            <button class="inv-sell" onclick="sellNFT('${i.name}',${i.value})">Продать</button>
        </div>
    `).join('');
}

async function sellNFT(name, value) {
    const res = await fetch(`${API}/sell_nft`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ uid: userId, name, value }) });
    const data = await res.json();
    if (data.success) { userBalance = data.balance; updateUI(); tg.showAlert('Продано!'); loadInventory(); }
}

// ==================== МАГАЗИН ====================
async function loadShop() {
    const res = await fetch(`${API}/shop`);
    const items = await res.json();
    document.getElementById('shop-list').innerHTML = items.map(i => `
        <div class="shop-item">
            <div class="nft-icon nft-${i.icon}"></div>
            <span class="shop-name">${i.name}</span>
            <span class="shop-price">${i.value}⭐</span>
            <button class="shop-buy" onclick="buyNFT('${i.name}',${i.value},'${i.icon}')">Купить</button>
        </div>
    `).join('');
}

async function buyNFT(name, value, icon) {
    const res = await fetch(`${API}/buy_nft`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ uid: userId, name, value, icon }) });
    const data = await res.json();
    if (data.success) { userBalance = data.balance; updateUI(); tg.showAlert('Куплено!'); loadInventory(); }
    else tg.showAlert('Недостаточно звёзд!');
}

// ==================== АПГРЕЙД ====================
async function selectFromNFT() {
    const res = await fetch(`${API}/inventory/${userId}`); const items = await res.json();
    if (items.length) { upgradeFrom = items[0]; document.getElementById('from-nft').textContent = items[0].name; updateWheel(); }
}
async function selectToNFT() {
    const res = await fetch(`${API}/shop`); const items = await res.json();
    if (items.length) { upgradeTo = items[0]; document.getElementById('to-nft').textContent = items[0].name; updateWheel(); }
}
function updateWheel() {
    if (upgradeFrom && upgradeTo) {
        const chance = Math.max(1, Math.min(50, Math.floor(upgradeFrom.value / upgradeTo.value * 100)));
        document.getElementById('upgrade-chance').textContent = chance + '%';
        document.querySelector('.wheel').style.setProperty('--p', chance + '%');
    }
}
async function doUpgrade() {
    if (!upgradeFrom || !upgradeTo) { tg.showAlert('Выберите NFT!'); return; }
    const res = await fetch(`${API}/upgrade`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ uid: userId, from: upgradeFrom, to: upgradeTo }) });
    const data = await res.json();
    tg.showAlert(data.won ? '🎉 Успех!' : '❌ Сгорел!');
    closeUpgrade();
}

// ==================== ЛИДЕРБОРД ====================
async function loadLeaderboard() {
    const res = await fetch(`${API}/leaderboard`);
    const data = await res.json();
    const list = document.getElementById('leaderboard-list');
    list.innerHTML = data.top.map((t, i) => `
        <div class="lb-row"><span class="lb-place">#${i+1}</span><div class="lb-avatar">${(t.name||'U')[0]}</div><span class="lb-name">${t.name||'User'}</span><span class="lb-amount">${t.total}⭐</span></div>
    `).join('');
}

// ==================== ОБРАБОТКА ОТВЕТОВ ====================
const origPostMessage = window.postMessage;
window.postMessage = function(msg, origin) {
    if (typeof msg === 'string' && msg.startsWith('case:')) {
        const parts = msg.split(':');
        if (parts[1] === 'error') {
            openingCase = false;
            document.getElementById('page-case-open').classList.remove('active');
            const err = parts[2];
            if (err === 'not_subscribed') tg.showAlert('Подпишитесь на @arcade_ludo!');
            else if (err === 'already_opened') tg.showAlert('Уже открывали!');
            else if (err === 'no_balance') tg.showAlert('Недостаточно звёзд!');
            else tg.showAlert('Ошибка!');
        } else if (parts[1] === 'success') {
            openingCase = false;
            userBalance = parseInt(parts[4]); updateUI();
            document.getElementById('case-result-block').style.display = 'block';
            document.getElementById('case-result-text').innerHTML = `<b>${parts[2]}</b><br>+${parts[3]}⭐`;
            setTimeout(() => {
                document.getElementById('page-case-open').classList.remove('active');
                document.getElementById('case-result-block').style.display = 'none';
                goPage('home');
            }, 3000);
        }
    }
    if (typeof msg === 'string' && msg.startsWith('withdraw:')) {
        const s = msg.split(':')[1];
        if (s === 'ok') tg.showAlert('✅ Заявка создана!');
        else if (s === 'min') tg.showAlert('❌ Минимум 100⭐');
        else if (s === 'no_balance') tg.showAlert('❌ Недостаточно звёзд!');
    }
    if (typeof msg === 'string' && msg.startsWith('ref:')) refCode = msg.split(':')[1];
    if (typeof msg === 'string' && msg.startsWith('promo:')) {
        const s = msg.split(':')[1];
        if (s === 'success') { userBalance += parseInt(msg.split(':')[2]); updateUI(); tg.showAlert(`+${msg.split(':')[2]}⭐!`); }
        else tg.showAlert('Промокод недействителен!');
    }
    origPostMessage.call(this, msg, origin);
};
