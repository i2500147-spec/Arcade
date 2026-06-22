const tg = window.Telegram.WebApp;
tg.expand();
tg.ready();

let userId = 0;
let userAvatar = '';
let currentCase = '';
let opening = false;

const initData = tg.initDataUnsafe;
if (initData.user) {
    userId = initData.user.id;
    userAvatar = initData.user.photo_url || '';
    document.getElementById('avatar').src = userAvatar;
    loadBalance();
}

function loadBalance() {
    tg.sendData(JSON.stringify({ action: 'get_balance' }));
}

function updateBalance(b) {
    document.getElementById('balance').textContent = b;
}

function goPage(page) {
    if (opening && page !== 'opening') return;
    
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    const el = document.getElementById('page-' + page);
    if (el) el.classList.add('active');
    
    document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
    const navMap = { home: 0, cases: 1, withdraw: 2 };
    if (navMap[page] !== undefined) {
        document.querySelectorAll('.nav-btn')[navMap[page]].classList.add('active');
    }
}

function switchTab(tab) {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    
    event.target.classList.add('active');
    document.getElementById('tab-' + tab).classList.add('active');
}

function setAmount(a) {
    document.getElementById('amount-input').value = a;
}

function buyStars() {
    const amount = parseInt(document.getElementById('amount-input').value);
    if (!amount || amount < 1) {
        tg.showAlert('Введите сумму!');
        return;
    }
    
    tg.sendData(JSON.stringify({ action: 'pay', amount: amount }));
    tg.showAlert('Счёт открыт в Telegram!');
    document.getElementById('amount-input').value = '';
}

function withdraw() {
    const amount = parseInt(document.getElementById('wd-amount').value);
    
    if (!amount || amount < 100) {
        tg.showAlert('Минимум: 100 ⭐');
        return;
    }
    
    tg.sendData(JSON.stringify({ action: 'withdraw', amount: amount }));
    document.getElementById('wd-amount').value = '';
}

function showCase(caseName) {
    currentCase = caseName;
    
    const cases = {
        daily: {
            icon: '📅', name: 'Ежедневный', price: 'БЕСПЛАТНО',
            loot: [
                ['⭐', '15 звёзд', '60%'],
                ['⭐', '30 звёзд', '20%'],
                ['⭐', '50 звёзд', '18%'],
                ['💎', '100 звёзд', '2%']
            ]
        },
        bum: {
            icon: '📦', name: 'Бомж', price: '5 ⭐',
            loot: [
                ['⭐', '1 звезда', '10%'],
                ['⭐', '5 звёзд', '60%'],
                ['⭐', '20 звёзд', '15%'],
                ['💫', '50 звёзд', '10%'],
                ['💎', '150 звёзд', '5%']
            ]
        },
        medium: {
            icon: '🎀', name: 'Среднячок', price: '50 ⭐',
            loot: [
                ['🌹', 'Роза (25 ⭐)', '25%'],
                ['🎂', 'Торт (50 ⭐)', '30%'],
                ['💍', 'Кольцо (100 ⭐)', '44%'],
                ['🍦', 'Нфт Мороженое (500 ⭐)', '1%']
            ]
        },
        major: {
            icon: '💎', name: 'Мажор', price: '350 ⭐',
            loot: [
                ['🐕', 'Нфт Снуп дог (1300 ⭐)', '10%'],
                ['🔥', 'Нфт Факел (450 ⭐)', '40%'],
                ['🧸', 'Мишка (15 ⭐)', '30%'],
                ['🍦', 'Нфт Мороженое (420 ⭐)', '10%']
            ]
        },
        allornothing: {
            icon: '🎰', name: 'Всё или ничего', price: '500 ⭐',
            loot: [
                ['🧸', 'Мишка (15 ⭐)', '30%'],
                ['🌹', 'Роза (25 ⭐)', '40%'],
                ['💎', '5000 звёзд', '25%'],
                ['👑', 'Премиум 3 мес', '5%']
            ]
        }
    };
    
    const c = cases[caseName];
    let html = `
        <div class="detail-icon">${c.icon}</div>
        <div class="detail-name">${c.name}</div>
        <div class="detail-price">${c.price}</div>
        <div class="loot-list">
    `;
    
    c.loot.forEach(item => {
        html += `
            <div class="loot-item">
                <span>${item[0]} ${item[1]}</span>
                <span class="loot-chance">${item[2]}</span>
            </div>
        `;
    });
    
    html += '</div><button class="btn-gold" onclick="openCase()">Открыть 🎁</button>';
    
    document.getElementById('detail-content').innerHTML = html;
    goPage('detail');
}

function openCase() {
    if (currentCase === 'daily') {
        tg.sendData(JSON.stringify({ action: 'check_sub' }));
    }
    
    opening = true;
    goPage('opening');
    document.getElementById('page-opening').classList.add('active');
    
    const track = document.getElementById('slot-track');
    const resultBlock = document.getElementById('result-block');
    
    track.classList.remove('spinning');
    resultBlock.style.display = 'none';
    
    void track.offsetWidth;
    track.classList.add('spinning');
    
    tg.sendData(JSON.stringify({ action: 'open_case', case: currentCase }));
    
    setTimeout(() => {
        track.classList.remove('spinning');
        resultBlock.style.display = 'block';
        document.getElementById('result-text').textContent = 'Ожидайте...';
    }, 3200);
}

function closeOpening() {
    if (opening) {
        tg.showAlert('Дождитесь завершения!');
        return;
    }
    goPage('cases');
}

function closeResult() {
    opening = false;
    document.getElementById('page-opening').classList.remove('active');
    goPage('cases');
    loadBalance();
}

// Обработка ответов от бота
window.addEventListener('message', function(e) {
    try {
        const data = JSON.parse(e.data);
        if (data.balance !== undefined) {
            updateBalance(data.balance);
        }
    } catch(err) {}
});

// Перехватываем ответы из Telegram
const origPostMessage = window.postMessage;
window.postMessage = function(msg, origin) {
    if (typeof msg === 'string' && msg.startsWith('balance:')) {
        updateBalance(parseInt(msg.split(':')[1]));
    }
    if (typeof msg === 'string' && msg.startsWith('case:')) {
        const parts = msg.split(':');
        const status = parts[1];
        
        if (status === 'error') {
            const errType = parts[2];
            opening = false;
            document.getElementById('page-opening').classList.remove('active');
            
            if (errType === 'not_subscribed') {
                tg.showAlert('Подпишитесь на @arcadeludo!');
            } else if (errType === 'already_opened') {
                tg.showAlert('Вы уже открывали сегодня!');
            } else if (errType === 'no_balance') {
                tg.showAlert('Недостаточно звёзд!');
            } else {
                tg.showAlert('Ошибка!');
            }
            goPage('cases');
        } else if (status === 'success') {
            const name = parts[2];
            const value = parts[3];
            const newBal = parts[4];
            
            document.getElementById('result-text').innerHTML = `🎉 <b>${name}</b><br>💰 +${value} ⭐`;
            updateBalance(parseInt(newBal));
        }
    }
    if (typeof msg === 'string' && msg.startsWith('sub:')) {
        // Ответ проверки подписки, кейс уже отправлен
    }
    if (typeof msg === 'string' && msg.startsWith('withdraw:')) {
        const status = msg.split(':')[1];
        if (status === 'ok') {
            tg.showAlert('✅ Заявка создана! Ожидайте 24 часа.');
            loadBalance();
        } else if (status === 'min') {
            tg.showAlert('❌ Минимум 100 ⭐');
        } else if (status === 'no_balance') {
            tg.showAlert('❌ Недостаточно звёзд!');
        }
    }
    origPostMessage.call(this, msg, origin);
};
