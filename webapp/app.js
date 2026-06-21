const tg = window.Telegram.WebApp;
tg.expand();
tg.ready();

let userId = 0;
let userAvatar = '';
let currentCase = '';

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
    tg.showAlert('Открываю счёт...');
    document.getElementById('amount-input').value = '';
}

function withdraw() {
    const amount = parseInt(document.getElementById('wd-amount').value);
    const card = document.getElementById('wd-card').value;
    
    if (!amount || amount < 100) {
        tg.showAlert('Минимум: 100 ⭐');
        return;
    }
    if (!card) {
        tg.showAlert('Введите номер карты!');
        return;
    }
    
    tg.sendData(JSON.stringify({ action: 'withdraw', amount: amount, card: card }));
    tg.showAlert('Заявка отправлена!');
    document.getElementById('wd-amount').value = '';
    document.getElementById('wd-card').value = '';
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
    goPage('opening');
    document.getElementById('page-opening').classList.add('active');
    document.getElementById('spinner').style.display = 'block';
    document.getElementById('result').style.display = 'none';
    
    const emojis = ['🎁', '💎', '⭐', '💫', '🪙', '👑', '💍', '🎉', '🔥', '🍦'];
    const spinner = document.getElementById('spinner');
    
    let count = 0;
    const interval = setInterval(() => {
        spinner.innerHTML = `<div style="font-size:80px;">${emojis[Math.floor(Math.random() * emojis.length)]}</div>`;
        count++;
        
        if (count > 25) {
            clearInterval(interval);
            
            tg.sendData(JSON.stringify({ action: 'open_case', case: currentCase }));
            
            setTimeout(() => {
                document.getElementById('spinner').style.display = 'none';
                document.getElementById('result').style.display = 'block';
                document.getElementById('result-icon').textContent = '🎉';
                document.getElementById('result-text').textContent = 'Кейс открыт! Проверьте бота.';
            }, 1500);
        }
    }, 80);
}

window.addEventListener('message', function(e) {
    try {
        const data = JSON.parse(e.data);
        if (data.balance !== undefined) {
            updateBalance(data.balance);
        }
    } catch(err) {}
});
